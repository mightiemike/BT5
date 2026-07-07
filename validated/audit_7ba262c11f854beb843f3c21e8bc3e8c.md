### Title
Hardcoded CREATE2 Salt in `createDirectDepositV1()` Allows Any Caller to Permanently DoS All Future DDA Deployments — (`core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.createDirectDepositV1()` is a `public` function that deploys a `DirectDepositV1` contract via CREATE2 using a **hardcoded salt of `bytes32(uint256(1))`**, regardless of the `subaccount` argument. Because the deployer address and salt are both fixed, the CREATE2 target address is always identical. Any caller who invokes `createDirectDepositV1` first occupies that address permanently, causing every subsequent call — for any other subaccount — to revert. This permanently DoS's `creditDepositV1` and `wrapVaultAsset` for all subaccounts that have not yet been initialized.

---

### Finding Description

In `ContractOwner.sol`, the function `createDirectDepositV1` is declared `public` with no access-control modifier:

```solidity
function createDirectDepositV1(bytes32 subaccount)
    public
    returns (address payable)
{
    require(
        getDirectDepositV1BytecodeHash() == 0x7974df41...,
        "dda hash"
    );
    DirectDepositV1 directDepositV1 = new DirectDepositV1{
        salt: bytes32(uint256(1))          // ← hardcoded, never changes
    }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
    directDepositV1Address[subaccount] = payable(directDepositV1);
    return payable(directDepositV1);
}
``` [1](#0-0) 

The CREATE2 address is a pure function of `(deployer, salt, initcodeHash)`. Because `deployer = address(ContractOwner)`, `salt = bytes32(uint256(1))` (constant), and `initcodeHash` is fixed (the bytecode hash check enforces this), the resulting address is **always the same** no matter which `subaccount` is passed.

The first successful call deploys `DirectDepositV1` at that address. Every subsequent call attempts to deploy to the same address, which already contains code, causing the EVM to revert the CREATE2 opcode. The mapping entry `directDepositV1Address[subaccount]` is never written for any later subaccount.

Both `creditDepositV1` and `wrapVaultAsset` lazily call `createDirectDepositV1` when no DDA exists for a subaccount:

```solidity
function creditDepositV1(bytes32 subaccount) external {
    address payable directDepositV1 = directDepositV1Address[subaccount];
    if (directDepositV1 == address(0)) {
        directDepositV1 = createDirectDepositV1(subaccount);   // reverts
    }
    DirectDepositV1(directDepositV1).creditDeposit();
}
``` [2](#0-1) 

```solidity
function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
    address payable directDepositV1 = directDepositV1Address[subaccount];
    if (directDepositV1 == address(0)) {
        directDepositV1 = createDirectDepositV1(subaccount);   // reverts
    }
    ...
}
``` [3](#0-2) 

---

### Impact Explanation

After the first `createDirectDepositV1` call (by anyone), every other subaccount is permanently unable to:

- Call `creditDepositV1` to credit collateral into their subaccount via the DDA flow.
- Call `wrapVaultAsset` to wrap vault assets into their subaccount.

These are the two externally reachable deposit paths that rely on DDA creation. The corrupted state is `directDepositV1Address[subaccount] == address(0)` for all subaccounts except the first, combined with a permanently occupied CREATE2 slot that can never be redeployed. Funds sent to the expected DDA address (which users may pre-compute) would be locked in a contract initialized for the wrong subaccount.

---

### Likelihood Explanation

The attack requires a single unprivileged transaction with no preconditions. `createDirectDepositV1` is `public` with no `onlyOwner` or similar guard. An attacker can call it with an arbitrary `subaccount` value at any time — including before any legitimate user — to occupy the CREATE2 slot. No mempool monitoring or front-running is even required; the attacker simply races to be first. On a live network this is trivially achievable. [4](#0-3) 

---

### Recommendation

1. **Include `subaccount` in the salt** so each subaccount gets a unique CREATE2 address:

```solidity
DirectDepositV1 directDepositV1 = new DirectDepositV1{
    salt: keccak256(abi.encode(subaccount))
}(address(endpoint), address(spotEngine), subaccount, wrappedNative);
```

2. **Restrict `createDirectDepositV1` to `internal` or add `onlyOwner`** to prevent arbitrary callers from deploying DDAs for subaccounts they do not own.

---

### Proof of Concept

1. Attacker calls `ContractOwner.createDirectDepositV1(attackerSubaccount)` directly (no permission required).
2. `DirectDepositV1` is deployed at `CREATE2(ContractOwner, salt=1, initcodeHash)` — call it `addrX`. `directDepositV1Address[attackerSubaccount] = addrX`.
3. Legitimate user calls `creditDepositV1(userSubaccount)`. Since `directDepositV1Address[userSubaccount] == address(0)`, it calls `createDirectDepositV1(userSubaccount)`.
4. The EVM attempts `CREATE2` with the same salt `1` → target address is `addrX` → `addrX` already has code → CREATE2 reverts.
5. `creditDepositV1` reverts. The user cannot deposit collateral via the DDA path. `wrapVaultAsset` fails identically.
6. This state is permanent: the CREATE2 slot is occupied and cannot be freed. [5](#0-4)

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
