Audit Report

## Title
Excess ETH Stranded in Router Consumed by Next WETH Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
Every payable entry-point in `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` accepts arbitrary `msg.value` without validating it against the intended WETH input amount. `PeripheryPayments.pay()` consumes the contract's entire native ETH balance (`address(this).balance`) when paying with WETH, not just the current caller's `msg.value`. Any ETH left behind by one caller is silently consumed by the next caller who swaps or adds liquidity with WETH as the input token, resulting in direct loss of user principal.

## Finding Description
In `PeripheryPayments.pay()`, when `token == WETH`, the function reads `address(this).balance` — the total native balance of the router — and uses it to wrap ETH before transferring to the pool:

```solidity
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
``` [1](#0-0) 

None of the payable entry-points enforce any relationship between `msg.value` and the intended input amount. `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `addLiquidityExactShares`, and `addLiquidityWeighted` are all `payable` with no guard: [2](#0-1) [3](#0-2) 

`refundETH()` exists but is never called automatically — it requires the user to explicitly include it in a `multicall` batch: [4](#0-3) 

**Exploit path:**
1. Victim calls `exactInputSingle{value: 1 ether}` with `params.amountIn = 0.5 ether` and `tokenIn = WETH`.
2. The swap callback triggers `pay()`, which sees `nativeBalance = 1 ether >= 0.5 ether`, wraps exactly 0.5 ETH, and sends it to the pool. The remaining 0.5 ETH stays in the router.
3. Attacker calls `exactInputSingle{value: 0}` with `params.amountIn = 0.5 ether` and `tokenIn = WETH`.
4. `pay()` sees `nativeBalance = 0.5 ether >= 0.5 ether`, wraps the victim's stranded ETH, and sends it to the pool on the attacker's behalf.
5. Attacker receives the full swap output; victim's 0.5 ETH is permanently lost.

The same loss occurs when a user sends ETH while swapping a non-WETH token: the ETH is accepted by the payable function, never used, and left for the next WETH caller.

## Impact Explanation
Direct loss of user principal. A victim who over-sends ETH (or sends ETH while swapping a non-WETH token) loses the excess to the next WETH caller. The attacker pays nothing for their input token. This meets the Sherlock threshold for Medium severity: High impact (direct loss of user funds), Low likelihood (requires user error or front-running of `refundETH()`).

## Likelihood Explanation
Low. The victim must either accidentally over-send ETH relative to `amountIn`, or send ETH when swapping a non-WETH token. Both are realistic user errors given that the functions are `payable` with no revert guard and `multicall` encourages batching patterns where ETH is sent once for multiple sub-calls. An attacker can monitor the mempool for transactions that leave a non-zero native balance in the router and front-run the victim's `refundETH()` call, or simply wait for the next WETH swap.

## Recommendation
1. When `tokenIn == WETH` in exact-input functions: require `msg.value == amountIn`.
2. When `tokenIn == WETH` in exact-output functions: require `msg.value >= amountInMaximum`, and refund the difference after the swap settles.
3. When `tokenIn != WETH`: require `msg.value == 0`.
4. Apply equivalent guards to `addLiquidityExactShares` and `addLiquidityWeighted` based on whether token0 or token1 is WETH.
5. Alternatively, always call `refundETH()` at the end of every payable entry-point unconditionally.

## Proof of Concept
```solidity
// Setup: Router deployed with WETH. Pool P has token0=WETH, token1=USDC.

// Step 1 — Victim over-sends ETH:
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: P, tokenIn: WETH, recipient: victim,
    amountIn: 0.5 ether, amountOutMinimum: 0,
    zeroForOne: true, priceLimitX64: 0,
    deadline: block.timestamp, extensionData: ""
}));
// pay() wraps 0.5 ETH, sends to pool. 0.5 ETH remains in router.
// assert(address(router).balance == 0.5 ether);

// Step 2 — Attacker steals the excess:
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: P, tokenIn: WETH, recipient: attacker,
    amountIn: 0.5 ether, amountOutMinimum: 0,
    zeroForOne: true, priceLimitX64: 0,
    deadline: block.timestamp, extensionData: ""
}));
// pay() sees nativeBalance=0.5 ether >= value=0.5 ether
// Wraps victim's 0.5 ETH, sends to pool on attacker's behalf.
// Attacker receives USDC output; victim's 0.5 ETH is gone.
// assert(address(router).balance == 0);
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-64)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
```
