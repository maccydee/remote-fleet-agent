# Privileged cleanup storage. Load only from the trusted installation kit or
# from the protected local copy installed alongside Remove-Assessment.ps1.
function Test-FleetStorageSecurity {
    param([string]$OwnerSid, $Rules, [switch]$Parent)
    $trusted=@('S-1-5-18','S-1-5-32-544')
    $parentOwners=$trusted+@('S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if($OwnerSid -notin $(if($Parent){$parentOwners}else{$trusted})){return $false}
    $mask=if($Parent){[int64]0x000D0040}else{[int64]0x000D0156}
    foreach($rule in @($Rules)) {
        if($rule.AccessControlType -ne 'Allow' -or ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)){continue}
        $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        # OWNER RIGHTS applies only to the owner validated above.
        if($sid -notin ($trusted+@('S-1-3-4')) -and ([int64]$rule.FileSystemRights -band $mask)){return $false}
    }
    return $true
}

function Assert-FleetStoragePath {
    param([Parameter(Mandatory)][string]$Path, [switch]$Parent)
    if(-not (Test-Path -LiteralPath $Path)){return}
    $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Privileged cleanup storage cannot use a reparse point: $Path"}
    $acl=Get-Acl -LiteralPath $Path -ErrorAction Stop
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if(-not (Test-FleetStorageSecurity -OwnerSid $owner -Rules $acl.Access -Parent:$Parent)) {
        throw "Untrusted owner or write permissions on cleanup storage: $Path. Use a trusted administrator to review this location before installation."
    }
}

function Assert-FleetStorageTree {
    param([Parameter(Mandatory)][string]$Path)
    $full=[IO.Path]::GetFullPath($Path)
    $parent=Split-Path $full -Parent
    while($parent) {
        Assert-FleetStoragePath -Path $parent -Parent
        $next=Split-Path $parent -Parent
        if($next -eq $parent){break}
        $parent=$next
    }
    Assert-FleetStoragePath -Path $full
    if(Test-Path -LiteralPath $full) {
        # Check each directory before enumerating it: never recurse into a
        # junction, and never read an untrusted saved rollback journal.
        foreach($child in @(Get-ChildItem -LiteralPath $full -Force -ErrorAction Stop)) {
            Assert-FleetStoragePath -Path $child.FullName
            if($child.PSIsContainer){Assert-FleetStorageTree -Path $child.FullName}
        }
    }
}

function Set-FleetStorageAcl {
    param([Parameter(Mandatory)][string]$Path, [switch]$Directory)
    Assert-FleetStoragePath -Path $Path
    $acl=if($Directory){New-Object Security.AccessControl.DirectorySecurity}else{New-Object Security.AccessControl.FileSecurity}
    $inherit=if($Directory){'OICI'}else{''}
    $acl.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;${inherit};FA;;;SY)(A;${inherit};FA;;;BA)")
    Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop
    Assert-FleetStoragePath -Path $Path
}

function New-FleetStorageDirectory {
    param([Parameter(Mandatory)][string]$Path)
    Assert-FleetStorageTree -Path $Path
    if(-not (Test-Path -LiteralPath $Path)) {
        $acl=New-Object Security.AccessControl.DirectorySecurity
        $acl.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)')
        # Supply the ACL at creation; do not create a writable gap in ProgramData.
        if([Environment]::Version.Major -le 4){$null=[IO.Directory]::CreateDirectory($Path,$acl)}
        else{[IO.FileSystemAclExtensions]::Create((New-Object IO.DirectoryInfo($Path)),$acl)}
    }
    Set-FleetStorageAcl -Path $Path -Directory
}
