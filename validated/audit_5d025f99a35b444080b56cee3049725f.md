### Title
Native ETH Permanently Locked in `DirectDepositV1` — No Viable Recovery Path Due to `ContractOwner` Missing `receive()` — (File: `core/contracts/DirectDepositV1.sol` / `core/contracts/ContractOwner.sol`)

---

### Summary

`DirectDepositV1` has a `receive() external payable` function that wraps incoming ETH into `wrappedNative`. A `withdrawNative()` recovery function exists, but it sends ETH to `msg.sender`. The only protocol-level caller of `withdrawNative()` is `ContractOwner.withdrawFromDirectDepositV1()`. `ContractOwner` has no `receive()` or `fallback()` function, making the ETH recovery path permanently broken. Any native ETH that ends up in a DDA — specifically via the constructor pre-funding vector enabled by the fixed CREATE2 salt — is irrecoverably locked.

---

### Finding Description

`DirectDepositV1` is deployed via CREATE2 with a **hardcoded salt of `bytes32(uint256(1))`**, making its address fully deterministic and predictable for any given `subaccount`: [1](#0-0) 

Because the address is predictable, an attacker (or anyone) can send ETH to the DDA address **before** the contract is deployed. When `createDirectDepositV1(subaccount)` is later called, the constructor runs and finds a non-zero ETH balance: [2](#0-1) 

If the `wrappedNative.call` fails (e.g., `wrappedNative` is not yet live, or the call reverts for any reason), the constructor **does not revert** — it emits `NativeTokenTransferFailed` and continues. The ETH remains in the DDA.

After deployment, the `receive()` function also accepts ETH and wraps it: [3](#0-2) 

The intended recovery path for stuck native ETH is `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))`: [4](#0-3) 

This calls `DirectDepositV1.withdrawNative()`, which sends ETH to `msg.sender` (i.e., `ContractOwner`): [5](#0-4) 

`ContractOwner` inherits only from `EIP712Upgradeable` and `OwnableUpgradeable` and declares **no `receive()` or `fallback()` function**: [6](#0-5) 

The ETH transfer from `DirectDepositV1` to `ContractOwner` will always fail. `withdrawNative()` reverts with `"Failed to transfer native token to owner"`, and the ETH is permanently locked in the DDA.

---

### Impact Explanation

Any native ETH that ends up in a `DirectDepositV1` contract — whether from the constructor pre-funding scenario or any other path — is permanently irrecoverable. The only designated recovery function (`withdrawFromDirectDepositV1` with `token == address(0)`) always reverts because `ContractOwner` cannot receive ETH. The funds are locked with no alternative withdrawal path.

---

### Likelihood Explanation

The CREATE2 salt is fixed at `bytes32(uint256(1))`, so the DDA address for any subaccount is publicly computable before deployment. An attacker or user can trivially pre-fund the address. The constructor's silent failure path (emit-and-continue on failed wrapping) means ETH can silently accumulate without triggering a revert. The broken recovery path is a structural code defect that is always present regardless of attacker action.

---

### Recommendation

1. Add a `receive() external payable {}` function to `ContractOwner` so it can receive ETH forwarded from `DirectDepositV1.withdrawNative()`.
2. Alternatively, modify `withdrawFromDirectDepositV1` to pull ETH directly to `msg.sender` (the multisig owner) rather than routing through `ContractOwner`.
3. Consider using a non-fixed, per-subaccount salt in the CREATE2 deployment to reduce address predictability and pre-funding risk.

---

### Proof of Concept

1. Compute the CREATE2 address for a target `subaccount`'s DDA using the fixed salt `bytes32(uint256(1))`, `ContractOwner` as deployer, and the `DirectDepositV1` initcode.
2. Send ETH to that address before `createDirectDepositV1(subaccount)` is called.
3. Call `ContractOwner.creditDepositV1(subaccount)` (public, no access control) — this triggers `createDirectDepositV1`, deploying the DDA. The constructor finds the pre-funded ETH and attempts to wrap it. If `wrappedNative.call` fails, ETH stays in the DDA and `NativeTokenTransferFailed` is emitted.
4. Call `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))` as the owner.
5. Observe revert: `DirectDepositV1.withdrawNative()` calls `ContractOwner.call{value: balance}("")`, which fails because `ContractOwner` has no `receive()` function, causing revert with `"Failed to transfer native token to owner"`.
6. ETH is permanently locked in the DDA.

### Citations

**File:** core/contracts/ContractOwner.sol (L21-21)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
```

**File:** core/contracts/ContractOwner.sol (L495-498)
```text
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
        directDepositV1Address[subaccount] = payable(directDepositV1);
```

**File:** core/contracts/ContractOwner.sol (L628-636)
```text
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
```

**File:** core/contracts/DirectDepositV1.sol (L52-60)
```text
        uint256 balance = address(this).balance;
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
        }
```

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/DirectDepositV1.sol (L108-112)
```text
    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```
