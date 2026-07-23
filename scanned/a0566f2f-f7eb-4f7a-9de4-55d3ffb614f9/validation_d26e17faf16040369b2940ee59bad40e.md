### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Curated-Pool Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the actual user. Because EOAs cannot call `pool.swap()` directly (the pool immediately calls `metricOmmSwapCallback` on `msg.sender`), any pool admin who wants EOA users to be able to swap must allowlist the router — which silently opens the pool to **every** user, defeating the allowlist entirely.

---

### Finding Description

**Root cause — wrong actor bound in the extension check.**

`SwapAllowlistExtension.beforeSwap` checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct — it is the only authorized caller of the extension). `sender` is the first argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call.

**Pool side — `sender` is always the direct caller of `swap()`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

**Router side — the router is the direct caller of `pool.swap()`:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The router calls `pool.swap()` directly; `msg.sender` inside the pool is the **router address**. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Why EOAs cannot bypass the router:**

`MetricOmmPool.swap()` unconditionally calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` to settle the input token. EOAs have no code and cannot implement this callback, so they **must** go through the router or another intermediary contract.

**The two failure modes:**

| Pool admin action | Outcome |
|---|---|
| Allowlists individual user addresses only | Router swaps revert for everyone (router not allowlisted); pool is unusable for EOAs |
| Allowlists the router to make the pool usable | Every user on-chain can swap; allowlist is completely bypassed |

There is no configuration that simultaneously (a) allows EOA users to swap via the router and (b) restricts access to a curated set of users.

---

### Impact Explanation

**Severity: High**

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market participants, or protocol-internal actors) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker needs no special access — they simply call `exactInputSingle` or `exactInput` on the public router. All pool liquidity is exposed to unauthorized swappers, violating the core invariant that curated pools enforce access control on the economically relevant actor.

---

### Likelihood Explanation

**Likelihood: High**

The router is the primary and intended user-facing swap interface. Pool admins who deploy a `SwapAllowlistExtension` and want their allowlisted users to be able to trade will naturally allowlist the router (or discover that without doing so, no EOA can swap at all). The bypass requires no privileged access, no special timing, and no complex setup — any user can call the public router functions.

---

### Recommendation

The extension must identify the **originating user**, not the intermediary. Two complementary approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the pool to trust the router's self-reported identity, which reintroduces the same unverified-header problem unless the extension also verifies that `sender` (the direct pool caller) is a trusted router.

2. **Check `sender` (the direct caller) against a router allowlist, then decode the real user from `extensionData` with a signature or trusted-forwarder pattern:** The extension first asserts `sender` is a known trusted router, then reads the real user from the signed payload.

3. **Preferred — restrict the allowlist check to direct pool callers only and require users to call the pool through a wrapper that preserves identity:** Deploy a thin per-user proxy or use ERC-2771 trusted forwarder semantics so the pool always sees the real user as `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (required so that any EOA can swap at all)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (EOA, not allowlisted) calls:
      router.exactInputSingle(ExactInputSingleParams({
          pool: pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      }))

Execution trace:
  router.exactInputSingle()
    → pool.swap(msg.sender=router, ...)
        → _beforeSwap(sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → allowedSwapper[pool][router] == true  ✓ (passes)
        → swap executes, attacker receives output tokens

Result:
  attacker successfully swaps on a pool that was supposed to block them.
  The allowlist check passed because it verified the router's address,
  not the attacker's address.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L256-263)
```text

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
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
