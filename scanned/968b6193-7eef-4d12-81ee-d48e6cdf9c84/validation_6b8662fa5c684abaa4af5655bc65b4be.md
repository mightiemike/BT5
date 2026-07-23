### Title
`SwapAllowlistExtension` Gates on Immediate Pool Caller Instead of End User, Enabling Router-Mediated Allowlist Bypass — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap(...)` call. When `MetricOmmSimpleRouter` is the caller, `sender` equals the router address, not the actual end user. A pool admin who allowlists the router (a natural action to enable router-based swaps) inadvertently opens the pool to every user, defeating the per-user curation the extension is meant to enforce.

---

### Finding Description

**Wrong-actor binding in `SwapAllowlistExtension`**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first parameter forwarded by the pool. The pool's `swap` function passes its own `msg.sender` as that first argument:

```solidity
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  recipient,
  ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards it unchanged:

```solidity
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router is the one that calls `pool.swap(...)`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the actual end user. The extension then evaluates `allowedSwapper[pool][router]` — a single boolean that covers every user who routes through that contract.

**Contrast with `DepositAllowlistExtension`**, which correctly ignores `sender` (the immediate caller) and checks `owner` (the actual position owner):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [5](#0-4) 

The deposit extension correctly identifies the economic actor (`owner`); the swap extension does not — it identifies the intermediary contract instead.

---

### Impact Explanation

A pool admin who configures a curated pool with `SwapAllowlistExtension` and then allowlists the `MetricOmmSimpleRouter` (a natural step to support router-based swaps) grants every user on the router the same access as an individually allowlisted address. Any non-allowlisted user can bypass the per-user swap gate by routing through `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist policy — the sole mechanism protecting a curated pool's swap access — is silently nullified. This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path circumvents the configured access control.

---

### Likelihood Explanation

Medium. The trigger is a pool admin allowlisting the router contract. This is a plausible and expected operational step for any curated pool that also wants to support the standard periphery router. The admin has no on-chain signal that doing so opens the pool to all users; the `SwapAllowlistExtension` interface and naming ("Gates `swap` by swapper address") imply per-user granularity that does not survive router indirection.

---

### Recommendation

Gate on the actual end user, not the immediate pool caller. Two concrete options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and extension.

2. **Align with `DepositAllowlistExtension`**: For swaps the economic payer is the entity that satisfies the swap callback. The router stores the real payer in its callback context (`msg.sender` at router entry). Expose that value through the callback or `extensionData` so the extension can check it.

Until fixed, document explicitly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and that per-user swap gating requires users to call the pool directly.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, address(router), true)
   — intending to enable router-based swaps for allowlisted users.
3. Attacker (address NOT individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool,
           recipient: attacker,
           zeroForOne: true,
           amountIn: X,
           ...
       })
4. Router calls pool.swap(...) — pool's msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension evaluates allowedSwapper[pool][router] == true → passes.
7. Swap executes. Attacker receives output tokens from a pool
   they were never individually authorized to trade on.
```

The bypass requires zero privileged access from the attacker; the only precondition is that the pool admin has allowlisted the router — a routine operational action for any pool that supports the standard periphery.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-39)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
