### Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool. The pool always passes `msg.sender` of `pool.swap` as that argument. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router**, not the actual user. The extension therefore checks whether the **router** is allowlisted, not whether the actual swapper is allowlisted. If the pool admin allowlists the router (which is required for any router-mediated swap to work), every non-allowlisted user can bypass the curated pool's access control by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool.

**How the pool populates `sender`:** [2](#0-1) 

The pool passes its own `msg.sender` — the direct caller of `pool.swap` — as `sender` to `_beforeSwap`.

**How the router calls `pool.swap`:** [3](#0-2) 

The router is `msg.sender` to the pool. The pool therefore passes `address(router)` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**The dilemma this creates for pool admins:**

- If the pool admin does **not** allowlist the router → all router-mediated swaps revert with `NotAllowedToSwap`, even for legitimately allowlisted users who prefer the router path.
- If the pool admin **does** allowlist the router (the only way to enable router-mediated swaps for legitimate users) → every non-allowlisted user can bypass the allowlist by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router.

The same wrong-actor binding applies to the multi-hop `exactInput` path: [4](#0-3) 

For intermediate hops the payer is `address(this)` (the router itself), so the extension again sees the router, not the user.

**`ExtensionCalling._beforeSwap` propagation (no transformation):** [5](#0-4) 

The `sender` value is forwarded verbatim; there is no mechanism to recover the original EOA.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd users, institutional partners, or protocol-controlled addresses) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The pool admin's access-control boundary is broken by a standard, publicly accessible periphery path. This qualifies as an admin-boundary break where an unprivileged path bypasses a factory/pool role check, and as broken core pool functionality (the allowlist guard silently fails open for all router-mediated swaps).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed by the protocol. Any user who reads the periphery interface will naturally use the router. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices. Likelihood is **High**.

---

### Recommendation

The extension must identify the **economic actor** (the EOA or contract that initiated the swap), not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the original initiator through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted, which can be enforced by checking `msg.sender` (the pool) and a factory-registered router registry.

2. **Check `sender` (the pool's `msg.sender`) only for direct pool calls; require router-mediated swaps to embed the real user in `extensionData` and verify the router's identity:** The extension reads the pool's factory, checks whether `sender` is a registered router, and if so decodes the real user from `extensionData`.

The simplest safe fix is to remove the router from the allowlist and require all curated-pool users to call `pool.swap` directly, but this breaks UX. The correct long-term fix is option 1 or 2 above.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin also allowlists the router so that allowedUser can use it.
// allowedSwapper[pool][router] = true
// allowedSwapper[pool][allowedUser] = false (not needed; router covers it)
// allowedSwapper[pool][attacker] = false

// Attacker bypasses the allowlist:
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: token0,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: "",
        deadline: block.timestamp
    })
);
// Extension checks allowedSwapper[pool][router] == true → passes.
// Attacker swaps successfully on a pool they are not authorized to use.
```

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
