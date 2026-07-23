Let me check how `_beforeSwap` passes the sender to the extension hook, and verify the `ExtensionCalling` contract.

### Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Allowing Non-Allowlisted Users to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument passed by the pool. The pool always passes its own `msg.sender` as that argument. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. The hook therefore checks whether the **router** is allowlisted, not whether the actual user is allowlisted. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for their allowlisted users), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain — direct swap:**

```
User → MetricOmmPool::swap()
         msg.sender = User
         _beforeSwap(sender=User, ...)
         SwapAllowlistExtension::beforeSwap(sender=User)
         checks allowedSwapper[pool][User]  ✓ correct
```

**Call chain — router-mediated swap:**

```
User → MetricOmmSimpleRouter::exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(...)
              msg.sender = Router
              _beforeSwap(sender=Router, ...)
              SwapAllowlistExtension::beforeSwap(sender=Router)
              checks allowedSwapper[pool][Router]  ✗ wrong identity
```

In `MetricOmmPool::swap`, the pool passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

The hook then checks the allowlist keyed by `msg.sender` (the pool) and `sender` (whoever called the pool): [3](#0-2) 

When the router calls the pool, `sender` is always the router's address, regardless of which end-user initiated the transaction. The hook has no way to recover the actual user.

The router's `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` all call `pool.swap()` directly as `msg.sender`: [4](#0-3) [5](#0-4) 

**Two concrete failure modes:**

| Scenario | Admin intent | Actual result |
|---|---|---|
| Router **not** allowlisted | Allow specific users to swap via router | Allowlisted users cannot use the router at all — broken UX |
| Router **is** allowlisted | Allow specific users to swap via router | **Any** user can bypass the allowlist by routing through the router |

The second scenario is the exploitable path: a pool admin who wants their allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the allowlist is completely ineffective for router-mediated swaps.

The "paused pool" angle in the question is a red herring. `swap` carries `whenNotPaused`: [6](#0-5) 

So `beforeSwap` is never reached while the pool is paused. `removeLiquidity` lacks `whenNotPaused` but only triggers `_beforeRemoveLiquidity`, never `_beforeSwap`. There is no paused-pool path to the hook.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for pools to restrict who may swap. When the router is involved, the hook cannot distinguish between different end-users — it only sees the router. A pool admin who allowlists the router to support router-mediated swaps for their approved users inadvertently opens the pool to **all** users. This constitutes broken core functionality: the allowlist extension does not enforce the invariant it was designed to enforce when the standard periphery router is used.

---

### Likelihood Explanation

Any pool that (a) uses `SwapAllowlistExtension` with `allowAllSwappers = false` and (b) allowlists the router to support normal UX is affected. This is the expected operational configuration for a restricted pool that still wants to support the standard router. The attacker needs no special privileges — they only need to call `MetricOmmSimpleRouter::exactInputSingle` (or any `exact*` variant) targeting the pool.

---

### Recommendation

The extension must verify the actual end-user, not the immediate caller. Two standard approaches:

1. **Pass the original `msg.sender` through `extensionData`**: The router encodes the real user in `extensionData`; the hook reads and verifies it. This requires router cooperation and is trust-dependent.

2. **Allowlist at the router level**: The router exposes a per-pool, per-user allowlist and enforces it before calling the pool. The pool-level hook then uses `allowAllSwappers = true`. This keeps the gate at the correct trust boundary.

3. **Reject router addresses in the hook**: The hook can maintain a registry of known routers and, when `sender` is a router, revert unless the router is explicitly trusted and the actual user is passed via `extensionData`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension, allowAllSwappers[pool] = false
  allowedSwapper[pool][alice] = true   (alice is the only approved swapper)
  allowedSwapper[pool][router] = true  (admin allowlists router so alice can use it)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, ...)   // msg.sender = router

  pool calls:
    extension.beforeSwap(sender=router, ...)

  hook checks:
    allowedSwapper[pool][router] == true  → PASSES

  bob receives output tokens despite never being allowlisted.

Assert:
  bob's swap succeeds.
  alice's direct swap also succeeds (allowedSwapper[pool][alice] = true).
  The allowlist has been bypassed for bob via the router.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
