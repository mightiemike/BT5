### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual user is allowlisted. Because the router must be allowlisted for any router-mediated swap to succeed on a restricted pool, allowlisting it grants every user on the public router the ability to bypass the per-user gate entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against its per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the value forwarded from the pool — the address that called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

When the router calls `pool.swap()`, `msg.sender` inside the pool is the **router address**. The pool therefore passes the router address as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same substitution occurs in `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput`.

The pool admin faces an impossible choice:

| Scenario | Result |
|---|---|
| Router **not** allowlisted | Allowlisted users cannot use the router at all |
| Router **allowlisted** | Every user on the public router bypasses the per-user gate |

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., a private OTC pool, a KYC-gated venue, or a protocol-internal pool) is fully bypassed by any user who calls through `MetricOmmSimpleRouter`. The attacker pays no extra cost and needs no special privilege. The pool's LP funds are exposed to unauthorized swap flow at oracle prices, which the pool admin explicitly intended to prevent. This constitutes an admin-boundary break: an unprivileged path (the public router) defeats a pool-admin-configured access control.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who discovers that a pool has a swap allowlist can trivially route through the router to bypass it. The router is a deployed, public, permissionless contract. No special capital, flash loans, or multi-block setup is required — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must recover the original end-user identity rather than trusting the `sender` argument, which reflects the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and verifies it. This requires the router to be trusted, which it already is for callback settlement.

2. **Check `recipient` instead of `sender`**: For swap allowlists the economically relevant actor is often the recipient of output tokens. If the pool's intent is to gate who receives funds, check `recipient` (the second argument to `beforeSwap`).

3. **Require direct pool calls only**: Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this by reverting when `sender` is a known router address.

The cleanest fix is option 1: the router should encode `msg.sender` into `extensionData` and the extension should decode and verify it, so the checked identity is always the end user regardless of intermediary.

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that legitimate users can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
// assertFalse(swapExtension.isAllowedToSwap(address(pool), alice));

// Alice calls the router — the extension sees sender = router (allowlisted), not alice.
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        recipient: alice,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds — allowlist bypassed.
```

The extension's `beforeSwap` evaluates `allowedSwapper[pool][router] == true` and passes, even though `allowedSwapper[pool][alice] == false`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
