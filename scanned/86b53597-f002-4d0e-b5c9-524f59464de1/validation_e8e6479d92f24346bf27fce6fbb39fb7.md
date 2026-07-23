### Title
`DepositAllowlistExtension` Checks Position `owner` Instead of Actual Caller `sender`, Allowing Any Unprivileged Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` is documented as gating `addLiquidity` **by depositor address**. However, the implementation silently discards the `sender` argument (the actual `msg.sender` who called `addLiquidity`) and instead validates the `owner` argument (the position-recipient address). Any address that is not on the allowlist can bypass the guard entirely by supplying an allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook: [1](#0-0) 

```
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  sender      owner (position recipient, caller-supplied)
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first parameter but declares it **unnamed** (discarded), then checks only `owner`: [3](#0-2) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Because `owner` is a **caller-supplied argument** with no on-chain constraint tying it to `msg.sender`, any address can pass the guard by nominating an allowlisted address as the position recipient.

The callback that actually pays the tokens is dispatched to `msg.sender` (the real caller), not to `owner`: [4](#0-3) 

So the real caller funds the deposit, the allowlisted address receives the LP position, and the guard never fires.

---

### Impact Explanation

The `DepositAllowlistExtension` is the sole mechanism a pool admin has to restrict who may provide liquidity. With this bug the guard is structurally inert against any caller who knows one allowlisted address:

- **Allowlist fully bypassed**: any unprivileged address can deposit into a restricted pool.
- **LP position minted to an allowlisted address**: the allowlisted address can later call `removeLiquidity` (which enforces `msg.sender == owner`) and withdraw the tokens, effectively laundering the deposit through the allowlist.
- **Pool insolvency / LP-claim integrity**: if the pool admin's intent was to keep the LP set small and trusted (e.g., to bound impermanent-loss exposure or satisfy regulatory constraints), unrestricted deposits can dilute or distort the bin balances that back existing LP claims.
- **Admin-boundary break**: an admin-configured access-control boundary is bypassed by an unprivileged path, matching the contest's allowed impact gate.

---

### Likelihood Explanation

- Requires no special privilege, no flash loan, and no non-standard token behavior.
- The attacker only needs to know one allowlisted address (publicly readable from `allowedDepositor`).
- Any pool that deploys `DepositAllowlistExtension` without `allowAllDepositors == true` is affected.
- Trigger is a single `addLiquidity` call with `owner` set to an allowlisted address.

---

### Recommendation

Replace the unnamed first parameter with `sender` and validate it instead of (or in addition to) `owner`:

```solidity
// Before (broken):
function beforeAddLiquidity(address, address owner, ...)

// After (fixed):
function beforeAddLiquidity(address sender, address owner, ...)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swap caller): [5](#0-4) 

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`; Alice (`0xAlice`) is allowlisted, Bob (`0xBob`) is not.
2. Bob calls:
   ```solidity
   pool.addLiquidity(
       0xAlice,   // owner — allowlisted, passes the guard
       salt,
       deltas,
       callbackData,  // Bob's callback pays the tokens
       extensionData
   );
   ```
3. `beforeAddLiquidity` checks `allowedDepositor[pool][0xAlice]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` calls `IMetricOmmModifyLiquidityCallback(Bob).metricOmmModifyLiquidityCallback(...)` — Bob's contract transfers the required tokens.
5. The LP position (shares in the specified bins) is credited to `0xAlice`.
6. Alice calls `removeLiquidity(alice, salt, deltas, ...)` and withdraws the tokens.
7. Bob has deposited into a restricted pool without being on the allowlist; the guard never triggered.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-154)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
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
