### Title
`ContractOwner` Cannot Receive Native Tokens, Permanently Locking ETH in `DirectDepositV1` — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary

`DirectDepositV1.withdrawNative()` transfers native tokens to `msg.sender` via a low-level `.call{value: ...}("")`. When invoked through `ContractOwner.withdrawFromDirectDepositV1`, the recipient of that transfer is `ContractOwner` itself. `ContractOwner` declares no `receive()` or `fallback()` function, so the transfer always reverts, permanently locking any native tokens held in `DirectDepositV1`.

---

### Finding Description

`DirectDepositV1` is deployed by `ContractOwner.createDirectDepositV1` and its Ownable owner is set to `ContractOwner`: [1](#0-0) 

The rescue path for stuck native tokens is `withdrawNative()`: [2](#0-1) 

When `ContractOwner.withdrawFromDirectDepositV1` calls this function with `token == address(0)`, the call stack is:

```
ContractOwner.withdrawFromDirectDepositV1
  └─> DirectDepositV1.withdrawNative()
        └─> msg.sender.call{value: balance}("")   // msg.sender == ContractOwner
``` [3](#0-2) 

`ContractOwner` has no `receive()` or `fallback()` function — confirmed by a full read of the file. The low-level call therefore fails, and `require(success, "Failed to transfer native token to owner")` reverts the entire transaction. [4](#0-3) 

---

### Impact Explanation

Any native tokens that accumulate in a `DirectDepositV1` instance cannot be recovered. Native tokens can reach `DirectDepositV1` in two documented ways:

1. **Constructor path** — if the initial wrapping call to `wrappedNative` fails, the balance is left in the contract and only an event is emitted (no revert): [5](#0-4) 

2. **`receive()` path** — if the `wrappedNative.call{value: msg.value}("")` inside `receive()` fails, the transaction reverts, but force-sends via `selfdestruct` bypass `receive()` entirely and deposit ETH with no wrapping. [6](#0-5) 

In both cases the only recovery mechanism is `withdrawNative()`, which is permanently broken. The asset delta is the full native token balance of every affected `DirectDepositV1` instance — permanently locked with no alternative withdrawal path.

---

### Likelihood Explanation

The `withdrawNative()` function is gated by `onlyOwner`, so only `ContractOwner` can call it. `ContractOwner.withdrawFromDirectDepositV1` is the sole caller. The broken invariant is structural and unconditional: every invocation of this path reverts. The likelihood of native tokens accumulating in a `DirectDepositV1` is non-zero given the constructor's silent-failure path and the possibility of force-sends.

---

### Recommendation

Add a `receive()` function to `ContractOwner` so it can accept native tokens forwarded from `DirectDepositV1.withdrawNative()`:

```solidity
receive() external payable {}
```

Alternatively, refactor `DirectDepositV1.withdrawNative()` to accept a `recipient` parameter and send directly to the intended EOA, bypassing `ContractOwner` as an intermediary:

```solidity
function withdrawNative(address payable recipient) external onlyOwner {
    uint256 balance = address(this).balance;
    (bool success, ) = recipient.call{value: balance}("");
    require(success, "Failed to transfer native token");
}
```

---

### Proof of Concept

1. A `DirectDepositV1` instance is deployed for some `subaccount`. During construction, if `wrappedNative.call{value: balance}("")` fails, ETH remains in the contract and `NativeTokenTransferFailed` is emitted silently.
2. The multisig owner calls `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))`.
3. `ContractOwner` calls `DirectDepositV1.withdrawNative()`.
4. Inside `withdrawNative()`, `msg.sender` is `ContractOwner`. The call `ContractOwner.call{value: balance}("")` fails because `ContractOwner` has no `receive()` function.
5. `require(success, "Failed to transfer native token to owner")` reverts.
6. The ETH remains permanently locked in `DirectDepositV1` with no alternative recovery path.

### Citations

**File:** core/contracts/ContractOwner.sol (L495-499)
```text
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
        directDepositV1Address[subaccount] = payable(directDepositV1);
        return payable(directDepositV1);
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
