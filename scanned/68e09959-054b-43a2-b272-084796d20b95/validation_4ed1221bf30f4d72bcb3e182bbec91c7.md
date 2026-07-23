### Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `MetricOmmPool.swap`. When users enter through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual user. If the pool admin allowlists the router — a natural action to enable router-mediated swaps for curated users — every unprivileged user can bypass the swap allowlist entirely.

---

### Finding Description

**Root cause — wrong actor bound to the allowlist check.**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of the pool: [4](#0-3) 

The full call chain is:

```
User → Router.exactInputSingle
         → Pool.swap(msg.sender = Router)
              → _beforeSwap(sender = Router)
                   → SwapAllowlistExtension.beforeSwap(sender = Router)
                        → allowedSwapper[pool][Router]   ← checks Router, not User
```

**Broken invariant.** The protocol's own audit target states:

> *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."*

The current implementation violates this: the identity checked by the hook changes depending on whether the user calls the pool directly or through the router.

**Concrete bypass path.** A pool admin who wants allowlisted users to be able to use the router has only one option: allowlist the router address. Doing so silently opens the gate to every user, because the extension cannot distinguish between router calls originating from allowlisted vs. disallowed users. There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks disallowed users from using the router.

**Analogy to the seed bug.** The NFTPositionManager bug used the wrong representation (asset balance instead of shares) for a critical accounting value, causing the guard to operate on stale data. Here, the wrong actor (router instead of actual user) is bound to the allowlist check, causing the guard to operate on the wrong identity. In both cases, users who interact through an intermediary receive systematically different treatment than the protocol intends.

---

### Impact Explanation

If a pool admin configures `SwapAllowlistExtension` to restrict swaps to a curated set of users and also allowlists the router to enable those users to trade via the standard periphery path, every unprivileged address can bypass the restriction by routing through `MetricOmmSimpleRouter`. Unauthorized swappers can then:

- Arbitrage the oracle-anchored pool and extract value from LPs.
- Front-run or sandwich allowlisted users.
- Drain one side of the pool's bin balances.

This is a direct loss of LP principal above Sherlock thresholds, triggered by a single public transaction with no special privileges.

---

### Likelihood Explanation

The scenario requires the admin to allowlist the router. This is the natural and expected action for any admin who wants their curated users to be able to use the standard periphery — there is no other way to enable router-mediated swaps for allowlisted users. The admin has no reason to suspect that allowlisting the router opens the gate to everyone, because the extension's NatSpec says it "gates `swap` by swapper address" without disclosing the router-identity collapse. The condition is therefore likely to be met in any production curated pool that also supports the router.

---

### Recommendation

The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Pass the real user in `extensionData`.** The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks that address instead of `sender`.

2. **Require direct pool calls for curated pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call the pool directly. This is a usability trade-off but eliminates the bypass.

The current design has no safe middle ground: allowlisting the router is a bypass, and not allowlisting it breaks router access for legitimate users.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as a beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
3. Admin calls setAllowedToSwap(pool, router, true)  // allowlist router so userA can use it
4. UserB (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(...) — msg.sender = router
6. SwapAllowlistExtension.beforeSwap(sender = router, ...)
       → allowedSwapper[pool][router] == true  → passes
7. UserB's swap executes successfully despite not being allowlisted.
```

The bypass is deterministic and requires no special state beyond step 3, which is the natural configuration for any admin who wants allowlisted users to use the router.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
