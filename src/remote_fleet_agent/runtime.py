from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.connection_pool import ConnectionPool
from remote_fleet_agent.jobs import JobManager
from remote_fleet_agent.maintenance import MaintenanceManager
from remote_fleet_agent.ssh_client import connect
from remote_fleet_agent.tools.collect import select_queries
from remote_fleet_agent.tools.diagnostics import QUERIES as DIAGNOSTICS
from remote_fleet_agent.tools.diagnostics import diagnostics
from remote_fleet_agent.tools.inventory import QUERIES as INVENTORY
from remote_fleet_agent.tools.inventory import inventory
from remote_fleet_agent.tools.readiness import QUERIES as READINESS
from remote_fleet_agent.tools.readiness import readiness

OPERATIONS = {
    "inventory": (INVENTORY, inventory),
    "diagnostics": (DIAGNOSTICS, diagnostics),
    "readiness": (READINESS, readiness),
}


class FleetRuntime:
    def __init__(self, config: FleetConfig, key_path: str, job_dir: Path):
        self.config = config
        if config.simulation:
            job_dir = job_dir / "simulation"

        async def connector(name, key):
            if config.simulation:
                raise ValueError("simulation never opens SSH connections")
            host = config.resolve(name)
            options = {"known_hosts": str(config.known_hosts)}
            if host.port != 22:
                options["port"] = host.port
            return await connect(host.target, key, **options)

        self.pool = ConnectionPool(connector, key_path)
        self.jobs = JobManager(job_dir, config.client, self.collect, config.concurrency)
        self.maintenance = MaintenanceManager(config, self.connection, job_dir)

    def hosts(self) -> list[dict]:
        return [asdict(host) for host in self.config.hosts]

    async def connection(self, host: str):
        return await self.pool.get(self.config.resolve(host).name)

    @staticmethod
    def catalog(platform, operation):
        if platform == "windows":
            return OPERATIONS[operation][0]
        from remote_fleet_agent.tools.unix import CATALOG

        return CATALOG[platform][operation]

    def require_windows(self, host):
        approved = self.config.resolve(host)
        if self.config.simulation or approved.platform != "windows":
            raise ValueError(
                "this legacy tool requires a real Windows host; use inventory, diagnostics or readiness"
            )
        return approved

    async def collect(
        self, host: str, operation: str, sections: list[str] | None = None
    ) -> dict:
        if operation not in OPERATIONS:
            raise ValueError("operation must be inventory, diagnostics or readiness")
        approved = self.config.resolve(host)
        queries = self.catalog(approved.platform, operation)
        select_queries(queries, sections)
        if self.config.simulation:
            from remote_fleet_agent.simulation import simulated_collection

            report = simulated_collection(approved, operation, sections)
            report.update(
                host=approved.name, target=approved.target, client=self.config.client
            )
            return report
        if approved.platform != "windows":
            from remote_fleet_agent.tools.unix import collect_unix

            report = await collect_unix(
                await self.connection(approved.name),
                approved.platform,
                operation,
                sections,
                self.config.query_timeout,
            )
            report.update(
                host=approved.name,
                target=approved.target,
                client=self.config.client,
                port=approved.port,
            )
            return report
        _, collector = OPERATIONS[operation]
        kwargs = {"client": self.config.client} if operation == "readiness" else {}
        report = await collector(
            await self.connection(approved.name),
            sections,
            self.config.query_timeout,
            **kwargs,
        )
        report.update(
            host=approved.name,
            target=approved.target,
            client=self.config.client,
            platform="windows",
            simulation=False,
            port=approved.port,
        )
        return report

    def start(
        self,
        operation: str,
        hosts: list[str] | None = None,
        group: str | None = None,
        sections: list[str] | None = None,
    ) -> dict:
        if operation not in OPERATIONS:
            raise ValueError("operation must be inventory, diagnostics or readiness")
        selected = self.config.select(hosts, group)
        for host in selected:
            select_queries(self.catalog(host.platform, operation), sections)
        return self.jobs.start([h.name for h in selected], operation, sections)

    def retry(self, job_id: str) -> dict:
        job, hosts = self.jobs.retry_hosts(job_id)
        # Re-check current approval; old evidence never grants new authority.
        selected = self.config.select(hosts=hosts)
        return self.jobs.start(
            [h.name for h in selected],
            job["operation"],
            job["sections"],
            parent_id=job_id,
        )

    async def close(self):
        await self.jobs.close()
        await self.pool.close_all()
