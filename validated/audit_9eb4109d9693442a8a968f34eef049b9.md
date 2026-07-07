### Title
`ContractOwner` Has No `receive()` Function, Permanently Bricking Native ETH Recovery from DirectDepositV1 — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner` calls `DirectDepositV1.withdrawNative()` to pull native ETH out of a DDA contract, but `ContractOwner` itself has no `receive()` or `fallback()` function. The low-level `.call{value: balance}("")` inside `withdrawNative()` targeting `ContractOwner` will always fail, causing `withdrawFromDirectDepositV1(subaccount, address(0))` to always revert and permanently locking any native ETH held in DDA contracts.

---

### Finding Description

`DirectDepositV1.withdrawNative()` sends the contract's full ETH balance to `msg.sender` via a low-level call: [1](#0-0) 

When invoked from `ContractOwner.withdrawFromDirectDepositV1`, `msg.sender` inside `withdrawNative()` resolves to the `ContractOwner` contract address: [2](#0-1) 

`ContractOwner` is an upgradeable contract that contains no `receive()` or `fallback()` function anywhere in its body: [3](#0-2) 

Because no ETH-accepting function exists on `ContractOwner`, the `.call{value: balance}("")` in `withdrawNative()` returns `success = false`. The hard `require(success, "Failed to transfer native token to owner")` then reverts the entire call: [4](#0-3) 

The `postBalance > preBalance` check in `ContractOwner` is never reached; the function is unconditionally broken for the native-ETH path.

---

### Impact Explanation

Any native ETH that accumulates inside a `DirectDepositV1` contract — whether force-sent via `selfdestruct`, left over from a failed `wrappedNative` wrap in the constructor, or stranded by a temporarily broken `wrappedNative` contract — cannot be recovered through the designated recovery path `withdrawFromDirectDepositV1(subaccount, address(0))`. The ETH is permanently locked in the DDA. There is no alternative on-chain path to extract it: `DirectDepositV1.withdrawNative()` is `onlyOwner` (owner = `ContractOwner`), and `ContractOwner` is the only caller that can invoke it. [1](#0-0) 

---

### Likelihood Explanation

The `DirectDepositV1` constructor already anticipates the scenario of ETH being present at deployment time and attempts to wrap it: [5](#0-4) 

If that wrap fails (emitting `NativeTokenTransferFailed` without reverting), ETH is silently stranded in the DDA. Additionally, any actor can force-send ETH to a DDA via `selfdestruct`. In both cases the only recovery mechanism — `withdrawFromDirectDepositV1` with `token == address(0)` — is permanently broken. The likelihood of ETH accumulating in a DDA is non-trivial given the explicit constructor handling for it.

---

### Recommendation

Add a `receive()` function to `ContractOwner` so it can accept the ETH forwarded by `DirectDepositV1.withdrawNative()`, and then forward it to `msg.sender` inside `withdrawFromDirectDepositV1`:

```solidity
// In ContractOwner.sol
receive() external payable {}
```

Alternatively, refactor `DirectDepositV1.withdrawNative()` to accept a `recipient` parameter so `ContractOwner` can direct the ETH straight to the multisig owner rather than routing it through itself.

---

### Proof of Concept

1. A `DirectDepositV1` DDA is deployed for some `subaccount` via `ContractOwner.createDirectDepositV1`.
2. ETH accumulates in the DDA — e.g., the `wrappedNative` call in the constructor fails silently (`NativeTokenTransferFailed` event emitted), leaving `balance > 0` in the DDA.
3. The multisig owner calls `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))`.
4. Internally, `DirectDepositV1(directDepositV1).withdrawNative()` is called.
5. Inside `withdrawNative()`, `(bool success, ) = msg.sender.call{value: balance}("")` targets `ContractOwner`, which has no `receive()` function → `success = false`.
6. `require(success, "Failed to transfer native token to owner")` reverts.
7. The ETH remains permanently locked in the DDA with no on-chain recovery path. [6](#0-5) [1](#0-0)

### Citations

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

**File:** core/contracts/DirectDepositV1.sol (L108-112)
```text
    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```

**File:** core/contracts/ContractOwner.sol (L21-22)
```text
contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
    error InvalidInput();
```

**File:** core/contracts/ContractOwner.sol (L622-636)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
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
