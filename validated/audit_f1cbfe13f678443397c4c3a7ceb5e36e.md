Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates `owner` (the LP position recipient). Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any unauthorized caller can bypass the allowlist by supplying an already-authorized owner address. The deposit allowlist guard is completely inoperative.

## Finding Description
`MetricOmmPool.addLiquidity` dispatches the hook as `_beforeAddLiquidity(msg.sender, owner, ...)` where `owner` is a caller-supplied argument with no enforced relationship to `msg.sender`: [1](#0-0) 

Contrast with `removeLiquidity`, which does enforce `msg.sender == owner`: [2](#0-1) 

`beforeAddLiquidity` receives `(address /*sender*/, address owner, ...)` — the first argument is unnamed and discarded — and performs the allowlist lookup on `owner`: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` demonstrates the correct pattern — it names and checks `sender`: [4](#0-3) 

The `isAllowedToDeposit` view function confirms the intended semantics accept a `depositor` parameter, but the hook checks the wrong field: [5](#0-4) 

**Exploit path:** Attacker calls `pool.addLiquidity(alice, salt, deltas, ...)` where `alice` is an authorized depositor. The pool dispatches `_beforeAddLiquidity(attacker, alice, ...)`. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert. The attacker successfully deposits; alice receives LP shares; the allowlist is bypassed entirely.

## Impact Explanation
The deposit allowlist guard is completely inoperative. Any address — regardless of allowlist status — can add liquidity to a pool the admin intended to restrict. This constitutes a broken admin-boundary: the pool admin's access control is bypassed by an unprivileged path. Secondary impact includes forced LP positions: an attacker can mint LP shares into any authorized owner's account without their consent, potentially griefing them with unwanted token exposure. Pools deployed for KYC, whitelist, or institutional-access purposes provide no actual restriction.

## Likelihood Explanation
Exploitation requires only a standard `addLiquidity` call with a known authorized owner address, which is publicly observable on-chain from prior deposits or admin configuration events (`AllowedToDepositSet` events). No special permissions, flash loans, or complex setup are needed. Any unauthorized party can trigger this immediately and repeatedly.

## Recommendation
Name and check `sender` instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` configured in `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is authorized.
3. Unauthorized `attacker` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Pool dispatches `_beforeAddLiquidity(attacker, alice, salt, deltas, extensionData)`.
5. `beforeAddLiquidity` evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. Attacker successfully deposits; alice receives LP shares; allowlist is bypassed.
7. Attacker can repeat with any authorized owner address, injecting liquidity into the restricted pool at will.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
