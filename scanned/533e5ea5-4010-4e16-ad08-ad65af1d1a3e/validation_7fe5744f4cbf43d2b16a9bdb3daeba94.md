### Title
`SwapAllowlistExtension.beforeSwap` gates the router's address instead of the actual user's address, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument it receives from the pool against the per-pool allowlist. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the router's address is what the extension evaluates — not the actual user. A pool admin who allowlists the router to permit router-mediated swaps inadvertently opens the gate to every user on-chain, completely defeating the per-user curation the extension was deployed to enforce.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol:230-240
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

When this call reaches the pool, `msg.sender` is the router contract, not the end user. The pool therefore passes the router's address as `sender` to the extension. The extension evaluates `allowedSwapper[pool][router]` — a slot that is either unset (blocking all router users, including allowlisted ones) or set to `true` by the admin (allowing every user on-chain to bypass the restriction).

The structural mismatch is identical to the `syncRamping` analog: the guard reads from the wrong source — the immediate caller of `pool.swap()` — rather than the economically relevant actor (the end user who initiated the transaction).

---

### Impact Explanation

**Allowlist bypass (high impact):** A pool admin who wants to permit router-mediated swaps calls `setAllowedToSwap(pool, routerAddress, true)`. Because the router is a public, permissionless contract, every user on-chain can now call `exactInputSingle` or `exactInput` and pass the extension check. The per-user curation is completely nullified; any non-allowlisted address can drain or trade against the pool's liquidity.

**Broken functionality (medium impact):** A pool admin who allowlists specific EOA addresses finds that those users cannot swap through the router at all (the router is not in the allowlist). Allowlisted users must implement `IMetricOmmSwapCallback` themselves to call `pool.swap()` directly, which is not the intended UX and effectively makes the pool unusable for normal participants.

Both outcomes represent direct loss of the pool's intended access-control invariant and, in the bypass case, direct loss of LP principal through unauthorized swaps.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will encounter this mismatch on the very first router-mediated swap. The bypass path requires no special timing, no privileged access, and no exotic token behavior — any user with a standard ERC-20 approval can exploit it the moment the router is allowlisted.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate pool caller. Two sound approaches:

1. **Pass the original `msg.sender` through the router.** The router already stores the payer in transient storage (`_getPayer()`). The pool could expose a `swapInitiator()` view during the swap action, or the router could pass the real user address inside `extensionData` in a standardized envelope that the extension decodes and verifies.

2. **Check `recipient` instead of `sender` when the pool is called by a known router.** This is weaker because `recipient` is also caller-controlled, but it avoids the router-identity problem for single-hop swaps.

The cleanest fix is option 1: the pool should forward the original initiator (stored in transient context by the router) as a dedicated field in the extension call, separate from the immediate `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, routerAddress, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attackerEOA, true).

Attack:
  1. Attacker (non-allowlisted EOA) calls
     MetricOmmSimpleRouter.exactInputSingle({pool, ...}).
  2. Router calls pool.swap(); pool's msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes; attacker receives output tokens.

Result:
  - Non-allowlisted attacker successfully swaps on a curated pool.
  - LP funds are exposed to any on-chain address via the public router.
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
