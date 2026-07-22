The attack path is mechanically valid. Here is the complete trace through the scoped code.

---

### Title
Unguarded `refundETH` drains any ETH left on the router to an arbitrary caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no per-depositor accounting. Because `MetricOmmSimpleRouter.multicall` is `payable` and `pay()` wraps only the exact amount needed for a swap, any excess `msg.value` remains on the router after the swap call completes. A third party who did not contribute any ETH can call `refundETH()` in a subsequent transaction and receive that balance.

---

### Finding Description

`refundETH()` is unconditional: [1](#0-0) 

It reads `address(this).balance` and forwards the entire amount to `msg.sender`. There is no mapping of depositor → amount, no `msg.sender` check against who sent the ETH, and no reentrancy guard.

`pay()` wraps **exactly** the swap-required amount when the native balance covers it: [2](#0-1) 

Any `msg.value` above that exact amount is left as raw ETH on the contract after the swap callback returns.

`multicall` is `payable` and uses `delegatecall`, so `msg.value` is visible to every sub-call but is not consumed or tracked: [3](#0-2) 

The `receive()` guard (only WETH can push ETH in) does not protect against ETH arriving via `msg.value` in a payable call: [4](#0-3) 

---

### Impact Explanation

**Direct loss of user principal.** User A sends `N + excess` ETH with a swap. After the swap, `excess` ETH sits on the router. Before User A calls `refundETH()`, User B front-runs with their own `refundETH()` call and receives `excess` ETH. User A's funds are permanently lost to User B. No privileged role, malicious pool, or non-standard token is required.

---

### Likelihood Explanation

Any user who sends more ETH than the exact swap amount — a common pattern when the exact input is not known ahead of time, or when using `exactOutputSingle` with a native-ETH input — is exposed. The attack window is the time between User A's swap transaction and their `refundETH()` transaction. A mempool-watching bot can reliably exploit this. Likelihood is **medium**: it requires User A to not bundle `refundETH()` atomically in the same multicall, but that is a realistic omission.

---

### Recommendation

1. **Preferred fix**: Track per-caller ETH deposits in transient storage at the start of each `multicall` / payable entry point and restrict `refundETH()` to return only the caller's recorded deposit.
2. **Minimum fix**: Enforce that `refundETH()` can only be called as part of a `multicall` (i.e., via `delegatecall`) so it is always atomic with the swap that created the excess, preventing standalone calls from a different `msg.sender`.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_refundETH_frontrun() public {
    // User A: swap 1 ETH worth, but sends 2 ETH
    bytes[] memory calls = new bytes[](1);
    calls[0] = abi.encodeCall(router.exactInputSingle, (paramsFor1ETH));
    router.multicall{value: 2 ether}(calls);
    // 1 ETH now sits on the router

    // User B: front-runs refundETH
    vm.prank(userB);
    router.refundETH();

    assertEq(userB.balance, 1 ether);   // User B stole User A's excess ETH
    assertEq(address(router).balance, 0);
}
```

The assertion passes because `refundETH()` sends `address(this).balance` to `msg.sender` (User B) with no check that User B contributed any ETH. [1](#0-0) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
