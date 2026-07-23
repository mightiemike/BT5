### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. The allowlist therefore gates the router's address, not the actual trader. Any user who is not individually allowlisted can bypass the restriction by calling through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

The pool therefore calls `_beforeSwap(router_address, ...)`, and the extension checks `allowedSwapper[pool][router_address]`.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert, even for individually allowlisted users |
| **Allowlist the router** | Every user on the network can swap through the router; the per-user allowlist is completely bypassed |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

The same structural problem applies to the multi-hop `exactInput` path: for every hop the router is `msg.sender` at the pool, so the extension sees the router address for all hops.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified institutions, protocol-owned addresses, or whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The LP providers in such a pool deposited under the assumption that only approved counterparties would trade against their liquidity. Unauthorized traders can now execute swaps against those LP positions, causing:

- Adverse selection losses for LP providers who expected a restricted counterparty set.
- Complete failure of the pool's curation invariant.
- Potential regulatory or compliance violations for pools that rely on the allowlist for legal reasons.

---

### Likelihood Explanation

- The router is the standard, publicly documented periphery entry point for all swaps.
- No special privileges, tokens, or setup are required — any EOA can call `MetricOmmSimpleRouter.exactInputSingle`.
- The bypass is deterministic and requires a single transaction.
- Pool admins who configure `SwapAllowlistExtension` have no on-chain mechanism to prevent router-mediated bypass without also blocking all router users.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router as `extensionData`** and have the extension verify it (requires a trusted router check, which introduces its own complexity).

2. **Check `recipient` instead of `sender`** if the economic intent is to gate who receives the output — but this does not gate who initiates the trade.

3. **Preferred: gate on `sender` but require direct pool calls for allowlisted pools.** Document that pools using `SwapAllowlistExtension` must not allowlist the router, and the router must not be used for such pools. Enforce this at the factory level by rejecting pool configurations that pair `SwapAllowlistExtension` with a router-compatible setup, or add a `trustedForwarder` pattern where the router attests the original `msg.sender` in a verifiable way.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true) — only Alice is allowed.
3. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
4. The router calls pool.swap(...) — msg.sender at the pool is the router.
5. The pool calls _beforeSwap(router_address, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router_address].
7. If the router is not allowlisted → Bob's swap reverts (but so does Alice's if she uses the router).
8. Pool admin, wanting Alice to use the router, calls setAllowedToSwap(pool, router, true).
9. Now Bob calls MetricOmmSimpleRouter.exactInputSingle again.
10. allowedSwapper[pool][router] == true → swap succeeds for Bob.
11. Bob, who was never individually allowlisted, has successfully traded on the curated pool.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
