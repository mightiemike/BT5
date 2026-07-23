### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address — not the actual end user. A pool admin who allowlists the router to enable router-mediated swaps for legitimate users simultaneously opens the pool to every user who routes through the same router, defeating the per-user curation the extension is designed to enforce.

---

### Finding Description

The pool's `swap` function passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — it does not forward the original caller's address to the pool: [4](#0-3) 

So when a user calls `exactInputSingle`, the pool receives `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

The pool admin faces an impossible choice:

- **Do not allowlist the router** → no router-mediated swap works for anyone, including legitimate allowlisted users.
- **Allowlist the router** → every user who calls the router can swap, regardless of whether they are individually allowlisted.

There is no configuration that simultaneously enables router-mediated swaps for allowlisted users and blocks non-allowlisted users, because the extension has no visibility into the original caller's identity.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` with the router as `msg.sender`: [5](#0-4) 

---

### Impact Explanation

A curated pool (e.g., KYC-only, institution-only, or regulatory-restricted) that relies on `SwapAllowlistExtension` can be accessed by any unprivileged user simply by routing through `MetricOmmSimpleRouter`. The bypass is direct and requires no special privileges: the attacker calls a public router function with a standard swap payload. The pool executes the swap and settles real token transfers, so the impact is direct loss of the curation guarantee and potential regulatory or financial harm to the pool and its LPs.

---

### Likelihood Explanation

Any pool admin who deploys `SwapAllowlistExtension` and also wants to support the standard periphery router must allowlist the router. This is the expected operational pattern for a production pool. The bypass is therefore reachable on every such pool without any special setup. The attacker needs only to call `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the curated pool.

---

### Recommendation

The extension must check the economically relevant actor — the original end user — not the intermediary. Two approaches:

1. **Pass original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and extension.

2. **Check `sender` against a router registry and fall back to a user-supplied identity**: The extension recognizes known routers and requires them to attest the real user via `extensionData`, while non-router callers are checked directly by `sender`.

The simplest safe fix is to not allowlist the router at the extension level and instead require users to call `pool.swap` directly, or to redesign the router to pass user identity in a way the extension can verify.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists Alice (allowedSwapper[pool][alice] = true)
  - Pool admin allowlists the router (allowedSwapper[pool][router] = true)
    so that Alice can use the router

Attack:
  1. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] = true → passes
  5. Swap executes; Bob receives output tokens from the curated pool

Result:
  Bob, who is not individually allowlisted, successfully swaps on the
  curated pool. The per-user allowlist is completely bypassed.
``` [6](#0-5) [7](#0-6)

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
