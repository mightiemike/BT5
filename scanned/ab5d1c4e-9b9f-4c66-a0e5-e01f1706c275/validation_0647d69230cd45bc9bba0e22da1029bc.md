### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Original Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the original user. The allowlist therefore gates the router address, not the economic actor. A pool admin who allowlists the router to let their approved users trade through the supported periphery path inadvertently opens the pool to **every user** who calls the router, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The lookup is `allowedSwapper[pool][router]`.

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool sees the router as `msg.sender`. The extension has no visibility into the original `msg.sender` of the router call.

**Consequence — two mutually exclusive failure modes:**

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all — the supported periphery path is broken for the curated pool |
| Router **allowlisted** (to fix the above) | Every user, including those explicitly denied, can bypass the allowlist by routing through `MetricOmmSimpleRouter` |

There is no configuration that achieves the intended semantics: "only approved users may swap, including through the router."

The `DepositAllowlistExtension` does **not** share this flaw — it gates on `owner` (the position holder), which is passed explicitly and is independent of the intermediary caller.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` for curation (institutional KYC, risk-gated access, or protocol-restricted liquidity) loses its access-control guarantee the moment the admin allowlists the router to restore router usability for approved users. Any unprivileged address can then call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on the restricted pool. This constitutes an admin-boundary break: an admin-configured access control is bypassed by an unprivileged path (the public router), causing the pool's curation invariant to fail and potentially exposing LP assets to unauthorized counterparties.

---

### Likelihood Explanation

The trigger path is realistic and follows a natural operational sequence:

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and populates `allowedSwapper` with approved addresses.
2. Approved users attempt to use `MetricOmmSimpleRouter` and receive `NotAllowedToSwap` (router not allowlisted).
3. Admin calls `setAllowedToSwap(pool, router, true)` — the obvious fix to restore router access.
4. All users can now bypass the allowlist through the router.

Step 3 is the natural remediation an admin would apply. The vulnerability is latent in the design and activates on a routine operational action.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **original user**, not the immediate pool caller. Two approaches:

1. **Pass the original user through the router**: Modify `MetricOmmSimpleRouter` to encode the original `msg.sender` in `extensionData`, and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when present.

2. **Check `sender` as the economic actor at the extension level**: Redefine the allowlist semantics so that `sender` (the first argument to `beforeSwap`) is always the original user. This requires the router to forward the original caller identity, which it currently does not do.

Either way, the extension must be able to distinguish the router (infrastructure) from the user (economic actor) and apply the allowlist to the latter.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   (alice is approved)
  allowedSwapper[pool][bob]   = false  (bob is denied)

Step 1 — alice tries router, fails:
  alice → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  pool.swap(msg.sender=router, ...)
  beforeSwap(sender=router) → allowedSwapper[pool][router] = false → NotAllowedToSwap ✗

Step 2 — admin allowlists router to fix alice's access:
  admin → setAllowedToSwap(pool, router, true)

Step 3 — bob bypasses allowlist:
  bob → MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  pool.swap(msg.sender=router, ...)
  beforeSwap(sender=router) → allowedSwapper[pool][router] = true → passes ✓
  bob executes swap on restricted pool despite being explicitly denied
```

**Root cause**: [1](#0-0)  checks `sender` which equals `msg.sender` at the pool level — the router address — not the originating user.

**Pool passes router as sender**: [2](#0-1) 

**Router calls pool directly with no user-identity forwarding**: [3](#0-2) 

**ExtensionCalling forwards `sender` unchanged**: [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
  ) internal {
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
