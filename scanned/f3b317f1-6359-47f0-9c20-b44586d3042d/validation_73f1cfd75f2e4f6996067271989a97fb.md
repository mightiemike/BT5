### Title
Unguarded `sweepToken` / `unwrapWETH9` Allow Any Caller to Drain Router Balance to an Arbitrary Recipient — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.sweepToken` and `PeripheryPayments.unwrapWETH9` are `public payable` with no access control and accept a fully caller-controlled `recipient` address. Any tokens or WETH that land in `MetricOmmSimpleRouter` or `MetricOmmPoolLiquidityAdder` — including intermediate multi-hop balances routed through `address(this)` — can be swept to an attacker's address by anyone who calls either function outside of the user's own `multicall` batch.

---

### Finding Description

`PeripheryPayments` exposes two balance-draining helpers:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol  L48-55
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) {
        IERC20(token).safeTransfer(recipient, balanceToken);
    }
}

// L37-45
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}
```

Both functions:
- carry **no `msg.sender` check** or ownership guard,
- sweep the **entire** contract balance (not a caller-specific amount), and
- forward funds to a **fully attacker-controlled** `recipient`.

`MetricOmmSimpleRouter` inherits `PeripheryPayments` and deliberately routes intermediate multi-hop balances through `address(this)`. In `exactInput`, every hop except the last sends output to the router:

```solidity
// MetricOmmSimpleRouter.sol  L103-112
_setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool).swap(
    i == last ? params.recipient : address(this),
    ...
);
```

The intended safe pattern is to batch `exactInput(recipient=router)` + `sweepToken`/`unwrapWETH9` inside a single `multicall`. But because both sweep functions are `public` with no guard, any tokens that reach the router — whether from a two-step user flow, an accidental direct transfer, or any other path — can be stolen by an attacker who calls `sweepToken(token, 0, attacker)` before the legitimate owner does.

---

### Impact Explanation

An attacker who observes a pending transaction that leaves tokens in the router (e.g., `exactInput` with `recipient: address(router)`) can front-run the user's follow-up `sweepToken` call and redirect **100% of the router's token balance** to their own address. The user loses their entire swap output. Because `sweepToken` sweeps the full balance rather than a caller-specific amount, a single attacker call is sufficient regardless of how many users' funds are pooled in the router at that moment.

---

### Likelihood Explanation

The `multicall` pattern (batch `exactInput` + `unwrapWETH9` in one tx) is the documented safe path, but:
- Nothing in the interface or NatSpec prevents a user from calling `exactInput(recipient=router)` in one transaction and `sweepToken` in a second.
- Integrators building on top of the router (e.g., aggregators, smart-contract wallets) may legitimately stage these as separate calls.
- Any accidental direct token transfer to the router is permanently at risk.

The attack requires only a standard mempool front-run with zero capital and no special permissions.

---

### Recommendation

Restrict `sweepToken` and `unwrapWETH9` so that the `recipient` can only be `msg.sender`, eliminating the ability for an attacker to redirect funds:

```solidity
function sweepToken(address token, uint256 amountMinimum) public payable {
    address recipient = msg.sender;
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);
    if (balanceToken > 0) IERC20(token).safeTransfer(recipient, balanceToken);
}
```

Alternatively, if a caller-specified recipient is required, enforce `recipient == msg.sender` or gate both functions behind an internal-only modifier so they are only reachable through `multicall` (which already executes as `delegatecall`, preserving `msg.sender`).

---

### Proof of Concept

```
1. Alice calls exactInput({
       tokens: [WETH, TOKEN],
       pools: [pool],
       recipient: address(router),   // output stays in router
       amountIn: 10_000,
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
   or silently transfers 0 if amountMinimum == 0.
   Alice's swap output is permanently lost.
```

**Affected contracts** (both inherit `PeripheryPayments`):
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2) 
- [4](#0-3) 

The router's intermediate-balance routing path that creates the exploitable window: [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-45)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
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
