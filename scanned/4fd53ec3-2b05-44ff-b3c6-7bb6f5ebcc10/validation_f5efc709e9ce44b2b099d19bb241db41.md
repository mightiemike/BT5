### Title
`SwapAllowlistExtension` Guard Bypassed via Router — Unauthorized Users Swap Against Gated LP Funds — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to gate pool swaps by individual swapper address. However, the pool always passes its own `msg.sender` (the router) as the `sender` argument to the extension hook. When the `MetricOmmSimpleRouter` is allowlisted, every user who routes through it bypasses the per-user restriction and can swap against LP funds the pool admin intended to protect.

---

### Finding Description

`MetricOmmPool.swap()` hardcodes `msg.sender` as the `sender` argument forwarded to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router becomes `msg.sender` at the pool level. The pool therefore passes the **router address** — not the actual end-user — to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for any pool admin who deploys this extension:

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user, regardless of individual allowlist status, can swap through the router — guard is fully bypassed |
| Router **is not** allowlisted | Individually allowlisted users cannot use the router at all — core swap path is broken |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

The `DepositAllowlistExtension` has a symmetric but distinct issue: it checks the caller-supplied `owner` parameter rather than `sender`, so any unpermissioned address can call `addLiquidity(owner = allowlistedAddress, ...)` and the check passes while the caller pays the tokens: [4](#0-3) 

The swap bypass is the higher-severity path because it directly exposes LP principal to unauthorized counterparties.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension`-gated pool and allowlists the router (the natural configuration to support router-based swaps) inadvertently grants every user on-chain the ability to swap against LP funds. The intended access control — restricting swaps to KYC'd addresses, whitelisted market makers, or any other curated set — is completely ineffective. Unauthorized users can extract value from LP positions at oracle-derived prices, causing direct loss of LP principal. This matches the "Allowlist path: cannot be bypassed through router" pivot and the "broken core pool functionality causing loss of funds" impact gate.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing interface documented in the periphery. Any pool admin who wants end-users to interact via the router must allowlist it. The bypass therefore activates under the most common production configuration. No privileged access, malicious setup, or non-standard token is required — a normal user calling the public router triggers it.

---

### Recommendation

The pool must forward the true initiating user to extensions, not the immediate `msg.sender`. Two complementary fixes:

1. **Router-level**: `MetricOmmSimpleRouter` should accept an explicit `sender` parameter (or use `msg.sender` internally) and pass it through `callbackData` or a dedicated `extensionData` field so extensions can recover the real user.

2. **Extension-level**: `SwapAllowlistExtension.beforeSwap` should decode the actual user from `extensionData` when the immediate `sender` is a known router, or the pool interface should be extended to carry a `msgSender` field distinct from the pool-level `msg.sender`.

Until fixed, pool admins should not rely on `SwapAllowlistExtension` for any pool accessible via the router.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.

2. Admin calls:
     extension.setAllowedToSwap(pool, router, true)
   — intending to allow router-based swaps for allowlisted users.

3. Admin calls:
     extension.setAllowedToSwap(pool, alice, true)
   — intending to restrict swaps to alice only.

4. Bob (not individually allowlisted) calls:
     MetricOmmSimpleRouter.swap(pool, ...)

5. Router calls pool.swap(...) → msg.sender in pool = router.

6. Pool calls _beforeSwap(router, recipient, ...).

7. Extension evaluates:
     allowAllSwappers[pool]          → false
     allowedSwapper[pool][router]    → true   ← router is allowlisted
   → check passes, no revert.

8. Bob's swap executes against LP funds.
   Alice's individual allowlist entry is irrelevant — the guard is bypassed.
```

The root cause is at: [5](#0-4) [6](#0-5)

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
