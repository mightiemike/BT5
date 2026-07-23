Audit Report

## Title
Stranded Native ETH Consumed by Subsequent WETH Swap Payments — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` reads `address(this).balance` unconditionally when settling a WETH payment, without distinguishing between ETH sent in the current transaction (`msg.value`) and ETH left over from a prior call. Any ETH stranded on the router from a previous transaction is silently consumed as part of the next user's WETH payment, causing the stranded-ETH owner to permanently lose those funds while the subsequent caller receives a subsidized swap.

## Finding Description

`pay()` in `PeripheryPayments.sol` (L73–84) handles WETH payments by reading `nativeBalance = address(this).balance`. When `0 < nativeBalance < value`, it wraps `nativeBalance` ETH into WETH, forwards it to the pool, then pulls only `value - nativeBalance` WETH from the actual payer. The pool receives the full `value`, but the payer only contributed `value - nativeBalance`; the difference was taken from whatever ETH happened to be sitting on the router.

ETH becomes stranded because all router entry points are `payable` (e.g., `exactInputSingle` at L67, `exactInput` at L92, `exactOutputSingle` at L130, `exactOutput` at L154, `multicall` at L39). A user who sends `msg.value = 150` for a swap that only needs `amountIn = 100` ETH worth of WETH will have 50 ETH remain on the router after the callback wraps and forwards exactly 100 ETH. The `receive()` guard at L32–34 only blocks plain ETH transfers from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` in payable function calls.

Without an automatic `refundETH()` call at the end of the swap, the 50 ETH is permanently stranded. Any subsequent caller whose WETH swap triggers `pay()` with `nativeBalance > 0` will consume that stranded ETH.

**Exploit path:**
1. User A calls `exactInputSingle{value: 150}(... tokenIn=WETH, amountIn=100 ...)`. The callback calls `pay(WETH, userA, pool, 100)`. `nativeBalance = 150 >= 100`, so 100 ETH is wrapped and forwarded. 50 ETH remains on the router. User A does not call `refundETH()`.
2. User B calls `exactInputSingle{value: 0}(... tokenIn=WETH, amountIn=100 ...)`. The callback calls `pay(WETH, userB, pool, 100)`. `nativeBalance = 50`, so the `else if (nativeBalance > 0)` branch fires: 50 ETH is wrapped and sent to the pool, then only 50 WETH is pulled from User B. User B's swap is fully settled; User A's 50 ETH is gone.

Existing guards are insufficient: `receive()` (L32–34) only blocks plain ETH pushes, not `msg.value` accumulation through payable entry points. There is no snapshot of `msg.value` at entry, no per-call ETH accounting, and no automatic refund.

## Impact Explanation

Direct, permanent loss of user principal. User A loses ETH they sent to the router; the loss equals the stranded amount and requires no privileged access. User B receives a subsidized swap at User A's expense. This is a direct loss of user principal above Sherlock thresholds, qualifying as High severity.

## Likelihood Explanation

ETH stranding is a realistic user error in Uniswap-style routers: users frequently send `msg.value` with WETH swaps to avoid a separate wrap step and omit `refundETH()` from their multicall. Any subsequent WETH swap by any other user will silently drain the stranded balance. An attacker can also monitor the router's ETH balance on-chain and time a WETH swap to capture stranded ETH opportunistically, requiring no special permissions.

## Recommendation

Track only the ETH that arrived in the current call. Snapshot `address(this).balance - msg.value` at entry and treat only `msg.value` as spendable native ETH in `pay()`. Alternatively, pass the spendable ETH amount explicitly through the callback context (e.g., stored in transient storage alongside the other callback fields) rather than reading the live contract balance. Also consider adding an automatic `refundETH()` at the end of each swap entry point, or enforcing that `msg.value == 0` when `tokenIn != WETH`.

## Proof of Concept

```solidity
function test_strandedEthConsumedByWethSwap() public {
    // 1. User A swaps with excess msg.value, forgets refundETH()
    vm.prank(userA);
    router.exactInputSingle{value: 150e18}(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 100e18, ...})
    );
    // Router now holds 50e18 stranded ETH
    assertEq(address(router).balance, 50e18);

    // 2. User B swaps WETH with no msg.value
    uint256 wethBefore = IERC20(WETH).balanceOf(userB);
    vm.prank(userB);
    router.exactInputSingle{value: 0}(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 100e18, ...})
    );

    // User B only spent 50 WETH (not 100); stranded ETH covered the rest
    assertEq(wethBefore - IERC20(WETH).balanceOf(userB), 50e18);
    // Router balance is now 0 — User A's 50 ETH is gone
    assertEq(address(router).balance, 0);
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
