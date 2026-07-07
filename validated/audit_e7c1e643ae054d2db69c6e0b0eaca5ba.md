### Title
`ContractOwner` Cannot Receive ETH, Permanently Locking Native Tokens in `DirectDepositV1` — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner` has no `receive()` or `payable fallback()` function. The `withdrawFromDirectDepositV1` recovery path for native ETH calls `DirectDepositV1.withdrawNative()`, which pushes ETH to `msg.sender` (i.e., `ContractOwner`). Because `ContractOwner` cannot accept ETH, the push reverts, and any native ETH stranded in a `DirectDepositV1` contract is permanently unrecoverable.

---

### Finding Description

`DirectDepositV1` is designed to accept native ETH via its `receive()` hook and immediately wrap it into `wrappedNative`. However, two concrete paths leave raw ETH inside the contract:

1. **Constructor wrapping failure** — the constructor attempts to wrap any pre-existing ETH balance, but on failure it only emits `NativeTokenTransferFailed` and continues, leaving the ETH in the contract.
2. **`selfdestruct` force-send** — ETH sent via `selfdestruct` bypasses `receive()` entirely.

To recover this ETH, `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))` is provided. Its logic is:

```solidity
// ContractOwner.sol L628-L636
uint256 preBalance = address(this).balance;
DirectDepositV1(directDepositV1).withdrawNative();
uint256 postBalance = address(this).balance;
require(postBalance > preBalance, "empty");
(bool success, ) = msg.sender.call{value: postBalance - preBalance}("");
require(success, "xfer");
```

`DirectDepositV1.withdrawNative()` pushes ETH to its caller (`ContractOwner`) via:

```solidity
// DirectDepositV1.sol L109-L111
(bool success, ) = msg.sender.call{value: balance}("");
require(success, "Failed to transfer native token to owner");
```

`ContractOwner` declares no `receive()` function and no `payable fallback()`. The entire contract file contains zero such definitions (confirmed: only `address payable` type annotations appear). The low-level `.call{value: ...}("")` to `ContractOwner` therefore reverts, causing `withdrawNative()` to revert with `"Failed to transfer native token to owner"`, which propagates up and makes `withdrawFromDirectDepositV1` always revert when `token == address(0)`.

---

### Impact Explanation

Native ETH that becomes stranded in any `DirectDepositV1` instance — whether from a constructor-time wrapping failure (explicitly anticipated by the `NativeTokenTransferFailed` event) or a force-send — is permanently locked. The designated recovery function `withdrawFromDirectDepositV1(..., address(0))` is unconditionally broken. There is no alternative recovery path in the codebase.

---

### Likelihood Explanation

The constructor of `DirectDepositV1` explicitly handles the wrapping-failure case with a non-reverting path and a dedicated event, demonstrating the protocol authors consider it a realistic scenario. Any deployment where `wrappedNative.call{value: balance}("")` fails at construction time (e.g., a paused or non-standard wrapped-native contract) will immediately strand ETH with no recovery. Force-send via `selfdestruct` is also always possible by any third party. Likelihood is **low-medium**: the failure condition is uncommon but explicitly anticipated, and the recovery function is completely inoperable.

---

### Recommendation

Add a `receive()` function to `ContractOwner`:

```solidity
receive() external payable {}
```

This mirrors the exact fix recommended in the referenced audit report (adding `payable` to the forwarding function) and unblocks the `withdrawFromDirectDepositV1` ETH recovery path.

---

### Proof of Concept

1. Deploy `DirectDepositV1` in a context where `wrappedNative.call{value: balance}("")` fails at construction (e.g., `wrappedNative` is a contract that reverts on ETH receipt). The constructor emits `NativeTokenTransferFailed` and leaves ETH in the `DirectDepositV1` contract.
2. Call `ContractOwner.withdrawFromDirectDepositV1(subaccount, address(0))`.
3. Internally, `DirectDepositV1.withdrawNative()` executes `msg.sender.call{value: balance}("")` targeting `ContractOwner`.
4. `ContractOwner` has no `receive()` — the call returns `success = false`.
5. `withdrawNative()` reverts: `"Failed to transfer native token to owner"`.
6. `withdrawFromDirectDepositV1` reverts. ETH is permanently locked.

**Exact root cause location:** [1](#0-0) 

`ContractOwner` missing `receive()` — confirmed by absence of any `receive()` or `fallback()` definition across the entire file: [2](#0-1) 

Constructor non-reverting ETH-stranding path (anticipated failure scenario): [3](#0-2)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L52-61)
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
        emit DirectDepositV1Created(version(), subaccount, address(this));
```

**File:** core/contracts/DirectDepositV1.sol (L108-112)
```text
    function withdrawNative() external onlyOwner {
        uint256 balance = address(this).balance;
        (bool success, ) = msg.sender.call{value: balance}("");
        require(success, "Failed to transfer native token to owner");
    }
```

**File:** core/contracts/ContractOwner.sol (L622-647)
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
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```
