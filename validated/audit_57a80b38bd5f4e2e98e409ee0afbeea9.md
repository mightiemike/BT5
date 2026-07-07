### Title
Hardcoded Bytecode Hash in `createDirectDepositV1` Will Brick Direct Deposit for All New Subaccounts — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.createDirectDepositV1` guards deployment of `DirectDepositV1` contracts with a hardcoded expected bytecode hash. If the compiled bytecode of `DirectDepositV1` ever diverges from that constant — due to a compiler upgrade, dependency change, or any code edit — the guard will always revert, permanently blocking the direct deposit mechanism for every new subaccount.

---

### Finding Description

`ContractOwner.createDirectDepositV1` contains the following guard:

```solidity
require(
    getDirectDepositV1BytecodeHash() ==
        0x7974df41bdca2be1539fa7d01f41277f0d728823b20230a18a31e40c707874e7,
    "dda hash"
);
``` [1](#0-0) 

`getDirectDepositV1BytecodeHash()` returns `keccak256(type(DirectDepositV1).creationCode)` — the hash of the creation bytecode as compiled into `ContractOwner` at build time. [2](#0-1) 

The hardcoded constant `0x7974df41...` is the expected hash from one specific compilation. Any change to `DirectDepositV1.sol`, its imports, the Solidity compiler version, or optimizer settings will produce a different hash. Because the constant is baked into the deployed `ContractOwner` bytecode and cannot be updated without a full redeployment, a mismatch causes `createDirectDepositV1` to revert unconditionally.

Both `creditDepositV1` and `wrapVaultAsset` call `createDirectDepositV1` lazily when `directDepositV1Address[subaccount] == address(0)`:

```solidity
function creditDepositV1(bytes32 subaccount) external {
    address payable directDepositV1 = directDepositV1Address[subaccount];
    if (directDepositV1 == address(0)) {
        directDepositV1 = createDirectDepositV1(subaccount);   // reverts
    }
    DirectDepositV1(directDepositV1).creditDeposit();
}
``` [3](#0-2) [4](#0-3) 

Both functions are `external` with no access restriction, so any user can call them. If the hash is stale, every call for a new subaccount reverts.

---

### Impact Explanation

Any user whose subaccount has not yet had a `DirectDepositV1` deployed will be permanently unable to:

- Call `creditDepositV1` to credit a pending native-token deposit.
- Call `wrapVaultAsset` to wrap a vault asset for deposit.

Funds that have already been sent to the expected `DirectDepositV1` address (computed deterministically via `CREATE2` with `salt = bytes32(uint256(1))`) will be locked in that undeployed address with no recovery path, because the deployment itself is blocked. [5](#0-4) 

---

### Likelihood Explanation

The hash was computed against a specific snapshot of `DirectDepositV1` and its dependency tree. Any routine maintenance — Solidity patch release, OpenZeppelin library bump, or a single-line change to `DirectDepositV1.sol` — silently invalidates it. Because `ContractOwner` is an upgradeable proxy, the hash constant cannot be patched without a full implementation redeployment, and there is no on-chain mechanism to update it. The risk is therefore present across every future upgrade cycle.

---

### Recommendation

Replace the hardcoded hash guard with a constructor/initializer parameter, mirroring the fix recommended in the referenced report:

```solidity
bytes32 public immutable expectedDdaHash;

function initialize(..., bytes32 _expectedDdaHash) external initializer {
    ...
    expectedDdaHash = _expectedDdaHash;
}

function createDirectDepositV1(bytes32 subaccount) public returns (address payable) {
    require(getDirectDepositV1BytecodeHash() == expectedDdaHash, "dda hash");
    ...
}
```

This allows the expected hash to be set at deployment time and updated via a new implementation if `DirectDepositV1` is ever changed, without hardcoding a value that silently becomes stale.

---

### Proof of Concept

1. Deploy the protocol with `ContractOwner` compiled against `DirectDepositV1` at commit X. The hardcoded hash matches.
2. Upgrade `DirectDepositV1` (e.g., bump the OpenZeppelin dependency or change a comment that affects metadata). Recompile and upgrade `ContractOwner` implementation.
3. `getDirectDepositV1BytecodeHash()` now returns a different value; the `require` at line 492 reverts.
4. Any user calling `creditDepositV1(subaccount)` for a new subaccount hits the revert. Funds already sent to the deterministic `CREATE2` address are permanently locked. [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L486-500)
```text
    function createDirectDepositV1(bytes32 subaccount)
        public
        returns (address payable)
    {
        require(
            getDirectDepositV1BytecodeHash() ==
                0x7974df41bdca2be1539fa7d01f41277f0d728823b20230a18a31e40c707874e7,
            "dda hash"
        );
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
        directDepositV1Address[subaccount] = payable(directDepositV1);
        return payable(directDepositV1);
    }
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/ContractOwner.sol (L510-514)
```text
    function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
```

**File:** core/contracts/ContractOwner.sol (L604-606)
```text
    function getDirectDepositV1BytecodeHash() public pure returns (bytes32) {
        return keccak256(type(DirectDepositV1).creationCode);
    }
```
