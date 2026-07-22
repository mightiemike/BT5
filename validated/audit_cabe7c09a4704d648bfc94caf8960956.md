### Title
SwapAllowlistExtension Per-User Gate Bypassed via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router, not the end user. If the pool admin allowlists the router (the only way to permit any router-mediated swap on an allowlisted pool), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong identity checked in the hook**

`SwapAllowlistExtension.beforeSwap` reads:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the caller of the extension) and `sender` is the first argument the pool passes to the hook. The pool always sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router. The extension therefore checks `allowedSwapper[pool][router]`, not the identity of the human caller.

**The forced admin choice that opens the bypass**

A pool admin who wants to allow router-mediated swaps on an allowlisted pool must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for every call that arrives through the router, regardless of who the original caller is. The admin has no mechanism to distinguish individual end-users at the extension level because the extension never sees the original caller — only the router address.

**`extensionData` does not help**

The pool forwards opaque `extensionData` to the hook, but `SwapAllowlistExtension.beforeSwap` ignores it entirely; it only reads `sender`. [3](#0-2) 

**Contrast with `DepositAllowlistExtension`**

The deposit-side extension correctly gates the `owner` (the position owner, the economically relevant party), not the payer/sender, so it is not affected by the same router-mediation problem. [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties). Any non-allowlisted user can bypass this restriction by calling `MetricOmmSimpleRouter`, which is a public periphery contract with no access control of its own. The bypass is complete: the non-allowlisted user receives the full swap output and the pool's LP positions are exposed to counterparties the admin explicitly intended to exclude. This constitutes a broken core pool access-control flow with direct fund-impact potential (LP assets traded against unintended counterparties).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract callable by any EOA or contract.
- The bypass requires only that the pool admin has allowlisted the router — a step the admin *must* take if they want any router-mediated swap to work on their allowlisted pool.
- No special privileges, flash loans, or oracle manipulation are needed.
- The attacker's only action is calling the router with a normal swap payload.

Likelihood is **high** whenever a pool is configured with `SwapAllowlistExtension` and the router is allowlisted.

---

### Recommendation

1. **Pass original caller through `extensionData`**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known router.

2. **Dedicated caller field in the hook interface**: Add an `originalCaller` field to the `beforeSwap` hook signature so the pool can propagate the true initiator independently of `msg.sender`.

3. **Router-level allowlist enforcement**: Add an allowlist check inside `MetricOmmSimpleRouter` itself that mirrors the pool's extension allowlist, so the router rejects non-allowlisted callers before reaching the pool.

4. **Documentation guard**: At minimum, document explicitly that `SwapAllowlistExtension` cannot gate individual users for router-mediated swaps, so admins do not deploy it with the false expectation of per-user control.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension as the before-swap hook.
2. Admin allowlists alice (KYC'd) for direct swaps:
       extension.setAllowedToSwap(pool, alice, true)
3. Admin allowlists MetricOmmSimpleRouter to enable router-mediated swaps:
       extension.setAllowedToSwap(pool, router, true)

Attack (bob is NOT allowlisted)
────────────────────────────────
4. bob calls MetricOmmSimpleRouter.exactInputSingle({pool, ...}).
5. Router calls pool.swap(recipient=bob, ..., callbackData, extensionData).
   → pool.msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension evaluates:
       allowedSwapper[pool][router] == true   ✓ passes
8. Swap executes; bob receives output tokens.
   → SwapAllowlistExtension never checked bob's address.

Result: bob, a non-allowlisted user, successfully swaps against the
allowlist-protected pool, defeating the admin's access-control intent.
``` [3](#0-2) [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
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
