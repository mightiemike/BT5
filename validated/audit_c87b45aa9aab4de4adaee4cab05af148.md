Audit Report

## Title
Unguarded `sweepToken` / `unwrapWETH9` Allow Any Caller to Drain Router Balance to an Arbitrary Recipient — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.sweepToken` and `unwrapWETH9` are `public payable` with no `msg.sender` check and accept a fully caller-controlled `recipient` address. Both functions sweep the **entire** contract balance rather than a caller-specific amount. Any tokens that land in `MetricOmmSimpleRouter` or `MetricOmmPoolLiquidityAdder` — including when a user deliberately routes final swap output to the router before calling `sweepToken` in a separate transaction — can be redirected to an attacker's address by anyone who calls either function first.

## Finding Description
`PeripheryPayments` exposes two unguarded balance-draining helpers:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol L37-45
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}

// L48-55
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);
    }
}
``` [1](#0-0) 

Neither function has any `msg.sender` check, ownership guard, or restriction on `recipient`. They sweep the full contract balance unconditionally.

`MetricOmmSimpleRouter.exactInput` deliberately routes intermediate multi-hop balances through `address(this)`:

```solidity
// MetricOmmSimpleRouter.sol L103-106
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool).swap(
    i == last ? params.recipient : address(this),
    ...
``` [2](#0-1) 

When a user sets `params.recipient = address(router)` in `exactInput` (the required pattern for a subsequent `unwrapWETH9` call to convert WETH output to ETH), the final swap output lands in the router. If the user then calls `sweepToken` or `unwrapWETH9` in a **separate** transaction rather than batching both inside `multicall`, an attacker can front-run the sweep call and redirect 100% of the router's token balance to themselves. The `multicall` implementation uses `delegatecall` and preserves `msg.sender`, but nothing in the interface, NatSpec, or contract logic enforces that `sweepToken`/`unwrapWETH9` are only reachable through `multicall`. [3](#0-2) 

Both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` inherit `PeripheryPayments` and expose these functions: [4](#0-3) [5](#0-4) 

## Impact Explanation
Direct loss of user principal. A user who performs a multi-hop swap ending in WETH and then calls `unwrapWETH9` in a second transaction loses their entire swap output to a front-runner. Because the sweep functions drain the full contract balance (not a per-caller amount), a single attacker call is sufficient regardless of how many users' funds are pooled in the router. This meets the Sherlock High severity threshold for direct loss of user funds with no special permissions required.

## Likelihood Explanation
The attack requires only a standard mempool front-run with zero capital and no special permissions. The two-step pattern (`exactInput(recipient=router)` then `sweepToken` in a separate tx) is a natural usage pattern for integrators, smart-contract wallets, and aggregators that stage calls across transactions. Nothing in the ABI, NatSpec, or revert logic warns callers that the two-step pattern is unsafe. Any accidental direct token transfer to the router is also permanently at risk.

## Recommendation
Restrict `sweepToken` and `unwrapWETH9` so that `recipient` can only be `msg.sender`:

```solidity
function sweepToken(address token, uint256 amountMinimum) public payable {
    address recipient = msg.sender;
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) IERC20(token).safeTransfer(recipient, balanceToken);
}

function unwrapWETH9(uint256 amountMinimum) public payable {
    address recipient = msg.sender;
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}
```

Since `multicall` uses `delegatecall`, `msg.sender` inside each batched call is the original caller, so this fix is fully compatible with the intended `multicall` batching pattern.

## Proof of Concept

```
1. Alice calls exactInput({
       tokens: [WETH, TOKEN],
       pools: [pool],
       recipient: address(router),   // final output stays in router
       amountIn: 10_000,
       amountOutMinimum: X,
       ...
   });
   // Router now holds TOKEN balance = swap output

2. Bob (attacker) observes Alice's pending follow-up call to
   sweepToken(TOKEN, 0, alice) in the mempool.

3. Bob front-runs with:
   sweepToken(TOKEN, 0, bob);
   // No access control — succeeds.
   // Router's entire TOKEN balance transferred to Bob.

4. Alice's sweepToken call reverts with InsufficientToken(alice, X, 0)
   (or silently transfers 0 if amountMinimum == 0).
   Alice's swap output is permanently lost.
```

Foundry test outline:
- Deploy router with a mock WETH and two mock pools.
- Call `exactInput` with `recipient = address(router)`.
- From a second address, call `sweepToken(token, 0, attacker)`.
- Assert attacker received the full balance and Alice's subsequent call transfers 0.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L19-19)
```text
contract MetricOmmSimpleRouter is MetricOmmSwapRouterBase, PeripheryPayments, SelfPermit, IMetricOmmSimpleRouter {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L22-22)
```text
contract MetricOmmPoolLiquidityAdder is IMetricOmmPoolLiquidityAdder, PeripheryPayments {
```
