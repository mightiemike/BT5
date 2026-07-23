### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` Per-User Gating — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the **router address**, not the original user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call path — direct swap:**

```
User → pool.swap()
  msg.sender in pool = User
  _beforeSwap(sender=User, ...)
  extension.beforeSwap(sender=User)  ← msg.sender=pool
  check: allowedSwapper[pool][User]  ✓ correct actor
```

**Call path — router-mediated swap:**

```
User → router.exactInputSingle()/exactInput()/exactOutputSingle()/exactOutput()
  router → pool.swap()
    msg.sender in pool = Router
    _beforeSwap(sender=Router, ...)
    extension.beforeSwap(sender=Router)  ← msg.sender=pool
    check: allowedSwapper[pool][Router]  ✗ wrong actor
```

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`: [3](#0-2) 

When the router calls `pool.swap()`, it is the direct `msg.sender` of that call: [4](#0-3) 

The router does not forward the original user's address as `sender` — it cannot, because `sender` is derived from `msg.sender` inside the pool, not from any caller-supplied parameter.

---

### Impact Explanation

A pool admin who wants to allow router-mediated swaps for allowlisted users must allowlist the router address. Once the router is allowlisted, **every user** — including those not individually allowlisted — can swap on the restricted pool by routing through the router. The per-user allowlist is completely ineffective for router-mediated flows. This is a policy bypass on curated pools: the economic action (swap) is attributed to the end user, but the authorization check gates the router instead.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard public entrypoint for swaps. Any pool that uses `SwapAllowlistExtension` and needs to support router-mediated swaps will encounter this. The attacker needs no special privileges — only access to the public router.

---

### Recommendation

The extension must recover the true originating user. Two options:

1. **Pass `tx.origin` as an additional parameter** — fragile and incompatible with smart-contract wallets.
2. **Require the router to embed the real payer in `extensionData`** and have the extension decode and verify it, with the router signing or committing to the payer identity in a way the extension can trust (e.g., the pool verifies the caller is a trusted router and reads the embedded address).
3. **Preferred:** Gate at the router level — the router checks allowlist membership for `msg.sender` before calling the pool, and the extension only needs to verify the caller is a trusted router. This separates routing authorization from pool-level authorization cleanly.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - Attacker calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - beforeSwap receives sender = router
  - allowedSwapper[pool][router] == true  → check passes
  - Attacker swaps successfully on a pool they were never individually authorized for
```

Direct assertion: `allowedSwapper[pool][attacker] == false` yet the swap succeeds, violating the invariant that every guard must key authorization to the same actor the economic action is attributed to. [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
