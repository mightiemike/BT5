### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the actual user. If the pool admin allowlists the router (required for router-mediated swaps to work on a curated pool), every user can bypass the individual allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.** [1](#0-0) 

`_beforeSwap` is called with `msg.sender` as the first argument. When the router calls `pool.swap()`, `msg.sender` = the router contract.

**Step 2 — `ExtensionCalling` forwards that value verbatim as `sender`.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the router.** [3](#0-2) 

`msg.sender` here is the pool; `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**Step 4 — The router calls `pool.swap()` with no user-identity forwarding.** [4](#0-3) 

The actual caller (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes; it is never passed to the pool or the extension.

**The dilemma this creates:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all (broken functionality) |
| **Allowlist the router** | Every user can bypass the individual allowlist by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or protocol-owned addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool's curation policy is silently defeated. Unauthorized users can drain liquidity, extract favorable oracle-priced trades, or front-run allowlisted users on a pool that was designed to be closed.

This is broken core pool functionality: the allowlist extension is the only mechanism for restricting swap access, and it fails open for the standard periphery path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented standard swap entry point. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. This is a natural operational step, not an exotic configuration. The bypass is therefore reachable on any curated pool that supports router-mediated swaps.

---

### Recommendation

The extension must check the actual user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router**: The extension maintains a registry of trusted routers; when `sender` is a router, it decodes the real user from `extensionData`.

3. **Restrict direct pool access**: Require all swaps to go through the router and have the router enforce the allowlist before calling the pool. This moves the gate to the periphery layer where the real user is known.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowlisted for alice to use it
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ..., extensionData)
6. Pool calls extension.beforeSwap(sender=router, ...)
7. Extension checks allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes on the curated pool, bypassing the allowlist.
``` [5](#0-4) [4](#0-3)

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
