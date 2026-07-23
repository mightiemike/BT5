### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of `MetricOmmPool.swap()`. When any user routes through `MetricOmmSimpleRouter`, `sender` equals the **router contract address**, not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for curated users inadvertently opens the pool to every user on-chain, bypassing the per-user allowlist entirely.

---

### Finding Description

**Root cause — wrong actor checked in the guard:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the end user
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

**The bypass path — MetricOmmSimpleRouter:**

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

From the pool's perspective, `msg.sender` = **router address**. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The forced admin dilemma:**

A pool admin who wants allowlisted users to be able to use the router has exactly two options:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | **Every** user on-chain can bypass the per-user allowlist |

There is no configuration that allows specific users through the router while blocking others, because the extension cannot distinguish between different end users who all arrive via the same router address. The same problem applies to `exactInput`, `exactOutput`, and `exactOutputSingle` — all router entry points call `pool.swap` as `msg.sender = router`.

**Contrast with DepositAllowlistExtension:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the economically relevant actor who receives LP shares), not on `sender` (the LiquidityAdder contract). The swap extension does not follow this pattern.

---

### Impact Explanation

If the pool admin allowlists the router (a natural operational step when onboarding curated users who expect to use the standard periphery), any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and trade against the pool's liquidity. The allowlist protection is completely nullified. LP funds are exposed to counterparties the pool was explicitly designed to exclude, which can cause direct LP principal loss on pools that rely on counterparty curation for their risk model.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical swap interface documented and expected by end users. A pool admin who deploys a curated pool and then wants allowlisted users to access it through the standard router will naturally add the router to the allowlist. The mistake is not obvious because the admin is adding a trusted periphery contract, not an arbitrary address. The bypass is then immediately available to any on-chain address with no further preconditions.

---

### Recommendation

Pass the original end-user address through the extension pipeline. Two viable approaches:

1. **Router-injected identity via `extensionData`:** Have the router encode `msg.sender` (the actual user) into `extensionData` before forwarding to the pool. The `SwapAllowlistExtension` decodes and checks this value instead of `sender`. The extension must verify the payload came from a trusted router (e.g., check `sender == trustedRouter`).

2. **Check `recipient` instead of `sender`:** For single-hop swaps where the user is also the recipient, checking `recipient` would gate the correct actor. This does not generalise to multi-hop paths where intermediate recipients are the router itself.

The cleanest fix is approach 1, which preserves the extension's ability to enforce per-user policies regardless of which periphery contract is used.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  admin allowlists userA (direct swap only)
  admin allowlists address(router) to enable router-mediated swaps for userA

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        recipient: userB,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  pool.swap() is called with msg.sender = router
  _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  Swap executes; userB receives output tokens

Result:
  userB bypassed the per-user allowlist entirely.
  Any address can repeat this for arbitrary swap amounts.
```

**Relevant code locations:**
- `SwapAllowlistExtension.beforeSwap` — check on wrong actor: [1](#0-0) 
- `MetricOmmPool.swap` — passes `msg.sender` as `sender`: [2](#0-1) 
- `ExtensionCalling._beforeSwap` — forwards `sender` unchanged: [3](#0-2) 
- `MetricOmmSimpleRouter.exactInputSingle` — router calls pool as `msg.sender`: [4](#0-3) 
- `DepositAllowlistExtension.beforeAddLiquidity` — correct pattern (checks `owner`, not `sender`): [5](#0-4)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
