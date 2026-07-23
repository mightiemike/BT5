### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which equals `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks the **router's address** against the allowlist rather than the actual user's address. If the pool admin allowlists the router — a natural action to enable router-mediated swaps for permitted users — every user who routes through the router bypasses the allowlist entirely.

---

### Finding Description

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of swap(), not the end-user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks: [2](#0-1) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (the extension's caller), and `sender` is the direct caller of `swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`: [3](#0-2) 

making the **router** the `sender`. The extension then evaluates `allowedSwapper[pool][router]`.

If the pool admin allowlists the router to enable router-mediated swaps for permitted users, the check becomes `allowedSwapper[pool][router] == true`, which passes for **all** users who route through the router, regardless of whether they are individually allowlisted.

The `ExtensionCalling._beforeSwap` dispatcher confirms the binding — `sender` is always the direct `msg.sender` of `swap()`, with no mechanism to carry the original end-user identity through the router hop: [4](#0-3) 

---

### Impact Explanation

Any user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. The allowlist's purpose — restricting swaps to specific permitted addresses — is completely defeated. Unauthorized users gain full swap access to pools intended to be restricted (e.g., KYC-gated, institutional-only, or risk-controlled pools). This breaks the core pool functionality of the allowlist extension and constitutes a broken-core-functionality finding under the contest's allowed impact gate.

---

### Likelihood Explanation

The pool admin must allowlist the router for this to be exploitable. This is a natural and expected action: if any allowlisted user wants to use the router (for multi-hop swaps, slippage protection, or permit-based flows via `selfPermit`), the admin must allowlist the router. The admin has no mechanism to allowlist the router for specific users only — allowlisting the router opens the gate to all users. The vulnerability is therefore reachable through normal, non-malicious pool administration.

---

### Recommendation

The `SwapAllowlistExtension` should check the actual end-user identity rather than the direct caller of `swap()`. Options:

1. **`extensionData` attestation**: Require the actual user address to be passed in `extensionData` (signed or attested by the router), and verify it against the allowlist inside `beforeSwap`.
2. **Documentation guard**: Explicitly document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this breaks the router UX for curated pools.
3. **Router-aware extension**: Have the router pass the originating user address in `extensionData`, and update `SwapAllowlistExtension` to decode and check that address when `sender` is a known router.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — allowlists user A.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlists the router so user A can use it.
4. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — `msg.sender` to the pool is the **router**.
6. Pool calls `SwapAllowlistExtension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. User B successfully swaps on the allowlisted pool, bypassing the allowlist entirely.

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
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
