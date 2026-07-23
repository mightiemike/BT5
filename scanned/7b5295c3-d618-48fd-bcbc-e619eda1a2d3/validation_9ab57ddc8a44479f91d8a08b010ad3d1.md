## Analysis

Let me trace the exact call chain to evaluate the claim.

**`SwapAllowlistExtension.beforeSwap` identity check:**

```solidity
function beforeSwap(address sender, ..., bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

- `msg.sender` = the pool (the extension's caller)
- `sender` = the first argument, which is `msg.sender` of `pool.swap()`

**`MetricOmmPool.swap` passes its own `msg.sender` as `sender`:**

```solidity
_beforeSwap(
    msg.sender,  // whoever called pool.swap()
    ...
    extensionData
);
```

**`MetricOmmSimpleRouter.exactInput` calls `pool.swap()` as the router:**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        MetricOmmSwapPath.openLimit(zeroForOne),
        "",
        params.extensionDatas[i]   // correctly indexed per hop
    );
```

When the router calls `pool.swap()`, the pool sees `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.

---

**On the "payload mismatch" claim specifically:** The `extensionDatas[i]` array is correctly indexed per hop in both `exactInput` and `exactOutput`. The `SwapAllowlistExtension` ignores the `bytes calldata` parameter entirely — it never reads `extensionData`. There is **no payload mismatch** — bytes are not consumed from the wrong step.

**On the identity substitution claim:** This IS real. The `sender` the hook sees is always the direct caller of `pool.swap()`. When the router intermediates, `sender = router`, not the original user.

**The concrete bypass scenario:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a whitelist of addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can reach the pool via the router.
3. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
4. The router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. The original user's identity is never checked. The per-user allowlist is fully bypassed.

**However**, this requires the pool admin to have allowlisted the router address. If the admin does NOT allowlist the router, router-mediated swaps by allowlisted users simply revert — the gate is too strict, not too loose. The bypass only materializes when the admin allowlists the router, which is the natural thing to do to enable router usage for a restricted pool.

**Payload mismatch across hops:** Does not occur. `params.extensionDatas[i]` is correctly delivered to hop `i` in `exactInput`; `params.extensionDatas[tradesLeftAfterThis]` and `cb.extensionDatas[tradesLeft]` are correctly delivered in `exactOutput`. The `SwapAllowlistExtension` ignores all payload bytes regardless.

---

### Title
Router Identity Substitution Bypasses SwapAllowlistExtension Per-User Gate — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates, `sender` becomes the router address. A pool admin who allowlists the router to enable router-mediated swaps for their whitelist inadvertently opens the pool to every user.

### Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap()`. [1](#0-0) 

The pool passes its own `msg.sender` as `sender` to `_beforeSwap`. [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension. [3](#0-2) 

The router calls `pool.swap()` directly, so the pool sees `msg.sender = router`. [4](#0-3) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`. If the admin allowlists the router, the per-user gate is bypassed for every caller. [5](#0-4) 

### Impact Explanation
Any unprivileged user can swap in a pool whose admin intended to restrict swaps to a specific whitelist, by routing through `MetricOmmSimpleRouter`. The allowlist invariant is broken: the hook cannot distinguish the original user from any other user once the router is allowlisted. This is a broken core functionality / admin-boundary break under the contest rules.

### Likelihood Explanation
A pool admin who wants allowlisted users to be able to use the router will naturally call `setAllowedToSwap(pool, router, true)`. The code and documentation give no indication that doing so opens the pool to all users. The scenario is realistic and requires no privileged attacker capability beyond calling the public router.

### Recommendation
The extension must verify the original user, not the intermediary. Options:
- Have the router encode the original `msg.sender` into `extensionData` and have the extension decode and verify it (requires a trusted router convention).
- Store the original caller in transient storage inside the pool and expose it to extensions.
- Document clearly that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`, so admins can make an informed choice.

### Proof of Concept
```
1. Pool deployed with SwapAllowlistExtension; allowedSwapper[pool][alice] = true.
2. Admin calls setAllowedToSwap(pool, router, true) to let Alice use the router.
3. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
4. Router calls pool.swap(...); pool passes msg.sender=router to _beforeSwap.
5. Extension checks allowedSwapper[pool][router] == true → passes.
6. Bob's swap executes in a pool he was never meant to access.
```

**Note on the payload mismatch framing:** The claim that "extension payload bytes are delivered to the wrong hop" is **not accurate**. `extensionDatas[i]` is correctly routed to hop `i` in both `exactInput` and `exactOutput`, and `SwapAllowlistExtension` ignores the payload bytes entirely. The real vulnerability is the identity substitution described above, which the question also asks about under "whether router-mediated swaps preserve that identity."

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

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
