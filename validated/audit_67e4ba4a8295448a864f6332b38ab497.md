### Title
Hardcoded `DirectDepositV1` Bytecode Hash Blocks All New Deposit Address Creation — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.createDirectDepositV1` guards deployment of `DirectDepositV1` contracts with a hardcoded bytecode hash. If the compiled bytecode of `DirectDepositV1` ever diverges from that hardcoded value (compiler upgrade, dependency bump, any source change), the guard reverts with `"dda hash"`, permanently blocking every unprivileged user from creating a deposit address, crediting deposits, or wrapping vault assets.

---

### Finding Description

In `ContractOwner.createDirectDepositV1`, before deploying a new `DirectDepositV1` instance, the contract asserts:

```solidity
require(
    getDirectDepositV1BytecodeHash() ==
        0x7974df41bdca2be1539fa7d01f41277f0d728823b20230a18a31e40c707874e7,
    "dda hash"
);
``` [1](#0-0) 

`getDirectDepositV1BytecodeHash()` computes `keccak256(type(DirectDepositV1).creationCode)` at runtime from the bytecode baked into the currently deployed `ContractOwner`. [2](#0-1) 

The hardcoded constant `0x7974df41...` is a compile-time snapshot. Any change to `DirectDepositV1` — a Solidity compiler version bump, an OpenZeppelin dependency update, a single-line source edit — produces a different creation-code hash. The `require` then fails unconditionally for every caller.

`createDirectDepositV1` is `public` with no access modifier. Both `creditDepositV1` and `wrapVaultAsset` are `external` with no access modifier, and each calls `createDirectDepositV1` lazily when `directDepositV1Address[subaccount] == address(0)`: [3](#0-2) [4](#0-3) 

---

### Impact Explanation

If the hash is stale:

- **All new subaccounts** are permanently blocked from obtaining a `DirectDepositV1` address.
- `creditDepositV1` reverts for every subaccount that has not yet been initialized, preventing collateral from being deposited into the protocol.
- `wrapVaultAsset` reverts for the same set of subaccounts, blocking ERC-4626 vault deposits.
- Existing subaccounts with a pre-deployed DDA are unaffected, but no new ones can ever be created.

The corrupted state is `directDepositV1Address[subaccount]` remaining `address(0)` forever, meaning the deposit flow is permanently broken for all new users.

---

### Likelihood Explanation

The `ContractOwner` contract is upgradeable (`OwnableUpgradeable`). A routine upgrade that touches `DirectDepositV1` — even an indirect one such as bumping the OpenZeppelin version used by `Ownable` — changes the creation code hash. The hardcoded value is never automatically updated. This is a realistic, low-effort trigger requiring no privileged access: any redeployment or upgrade cycle that modifies `DirectDepositV1` silently breaks the check.

---

### Recommendation

Remove the hardcoded hash guard entirely, or replace it with a mechanism that does not rely on a compile-time constant embedded in a separate upgradeable contract. If the intent is to prevent deployment of an unexpected `DirectDepositV1` implementation, derive the expected hash dynamically (e.g., store it as an upgradeable state variable that is updated atomically with any `DirectDepositV1` source change) rather than hard-coding it.

---

### Proof of Concept

1. `DirectDepositV1.sol` is modified (e.g., OpenZeppelin `Ownable` is bumped from 4.x to 5.x).
2. `ContractOwner` is upgraded with the new `DirectDepositV1` bytecode linked in.
3. `keccak256(type(DirectDepositV1).creationCode)` now returns a value ≠ `0x7974df41...`.
4. Any user calls `creditDepositV1(subaccount)` for a fresh subaccount.
5. `directDepositV1Address[subaccount] == address(0)` → `createDirectDepositV1` is called.
6. The `require` at line 490–494 reverts with `"dda hash"`.
7. The user's deposit is permanently blocked; no `DirectDepositV1` can ever be created again. [5](#0-4)

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

**File:** core/contracts/ContractOwner.sol (L510-515)
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
