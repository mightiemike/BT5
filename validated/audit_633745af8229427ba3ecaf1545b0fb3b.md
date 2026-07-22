### Title
Excess ETH Sent to Payable Swap/Liquidity Functions Stranded in Router and Stealable by Next WETH Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

Every payable entry-point in `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` accepts arbitrary `msg.value` without validating that it equals the intended WETH input amount, or that it is zero when the input token is not WETH. The shared `PeripheryPayments.pay()` helper consumes the contract's entire native balance opportunistically. Any ETH left behind by one caller is silently consumed by the next caller who swaps or adds liquidity with WETH as the input token, giving that caller a free ride at the victim's expense.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH payment by first checking the contract's current native ETH balance: [1](#0-0) 

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
```

`address(this).balance` is the **total** native balance of the router — it is not scoped to the current caller's `msg.value`. When a caller sends more ETH than the pool requests (or sends ETH while swapping a non-WETH token), the surplus remains in the contract.

None of the payable entry-points enforce any relationship between `msg.value` and the intended input amount: [2](#0-1) 

`exactInputSingle` is `payable` and accepts `params.amountIn` as a user-controlled value with no check that `msg.value == params.amountIn` (when `tokenIn == WETH`) or `msg.value == 0` (when `tokenIn != WETH`). The same applies to `exactInput`, `exactOutputSingle`, `exactOutput`, and `multicall`. [3](#0-2) 

`addLiquidityExactShares` and `addLiquidityWeighted` are also `payable` with no such validation.

The `refundETH()` helper exists but is never called automatically; it requires the user to explicitly include it in a `multicall` batch. Any user who does not know to do so, or whose transaction is front-run between the swap and the refund, loses the excess ETH permanently to the next WETH caller. [4](#0-3) 

---

### Impact Explanation

**Direct loss of user principal.** A victim who sends `msg.value = 1 ETH` while swapping only `0.5 ETH` of WETH loses `0.5 ETH` to the next caller who swaps WETH with `msg.value = 0`. The attacker's swap is funded entirely by the victim's excess ETH; the attacker pays nothing for their input token. The same loss occurs when a user sends ETH while swapping a non-WETH token: the ETH is accepted by the payable function, never used for that swap, and left for the next WETH caller to consume.

---

### Likelihood Explanation

**Low.** The victim must either accidentally over-send ETH relative to `amountIn`, or send ETH when swapping a non-WETH token. Both are user errors, but they are realistic given that the functions are `payable` with no revert guard, and that `multicall` encourages batching patterns where ETH is sent once for multiple sub-calls. An attacker can monitor the mempool for transactions that leave a non-zero native balance in the router and front-run the victim's `refundETH()` call (or simply wait for the next WETH swap).

---

### Recommendation

1. **When `tokenIn == WETH`**: require `msg.value == amountIn` (for exact-input) or `msg.value >= amountInMaximum` (for exact-output, with a refund of the difference after the swap settles).
2. **When `tokenIn != WETH`**: require `msg.value == 0`.
3. Apply the same guards to `addLiquidityExactShares` and `addLiquidityWeighted` based on whether token0 or token1 is WETH.
4. Alternatively, always call `refundETH()` at the end of every payable entry-point (not just when the user remembers to include it in a multicall).

---

### Proof of Concept

**Setup**: Router deployed with `WETH = address(weth)`. Pool `P` has `token0 = WETH`, `token1 = USDC`.

**Step 1 — Victim over-sends ETH:**
```solidity
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: P,
    tokenIn: WETH,
    recipient: victim,
    amountIn: 0.5 ether,   // only 0.5 ETH needed
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// pay() wraps 0.5 ETH, sends to pool. 0.5 ETH remains in router.
// address(router).balance == 0.5 ether
```

**Step 2 — Attacker steals the excess:**
```solidity
// Attacker sends msg.value = 0, amountIn = 0.5 ether
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: P,
    tokenIn: WETH,
    recipient: attacker,
    amountIn: 0.5 ether,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// pay() sees nativeBalance = 0.5 ether >= value = 0.5 ether
// Wraps victim's 0.5 ETH, sends to pool on attacker's behalf.
// Attacker receives USDC output; victim's 0.5 ETH is gone.
``` [5](#0-4) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
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
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
