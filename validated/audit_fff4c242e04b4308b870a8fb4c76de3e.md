### Title
`SwapAllowlistExtension` checks the immediate pool caller (router) instead of the end-user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router (the natural step to enable router-mediated swaps for permitted users), every unpermitted user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap()` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap()`.

In `MetricOmmPool.swap()`, the `sender` forwarded to the extension is `msg.sender` of the pool call:

```solidity
_beforeSwap(
    msg.sender,   // <-- becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

And `ExtensionCalling._beforeSwap()` passes it verbatim:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [3](#0-2) 

**Direct call path**: `user → pool.swap()` → extension sees `sender = user` → `allowedSwapper[pool][user]` is checked. ✓

**Router call path**: `user → router → pool.swap()` → extension sees `sender = router` → `allowedSwapper[pool][router]` is checked. ✗

The pool admin who wants to support the standard periphery flow must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through it, regardless of whether that individual user is on the allowlist.

The `DepositAllowlistExtension` does not share this flaw — it checks the `owner` argument (the LP position owner), which is user-supplied and preserved through the liquidity adder path:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [4](#0-3) 

The swap extension has no equivalent mechanism to recover the true end-user identity.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. The disallowed user can execute swaps at the oracle-anchored bid/ask, draining LP principal at the configured price, which is a direct loss of LP assets and a broken core pool invariant (the allowlist guard fails open).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the documented, supported periphery entrypoint for swaps. Any pool admin who enables router-mediated swaps for their allowlisted users must allowlist the router address, which simultaneously opens the bypass for all other users. The trigger requires no privileged access — any unpermitted user can call the public router.

---

### Recommendation

The extension must gate on the actual end-user identity, not the immediate pool caller. Two sound approaches:

1. **Transient-storage forwarding**: Have the router write the originating user address into transient storage before calling the pool, and have the extension read it. The pool's `nonReentrant` guard already uses transient storage (`TSTORE`/`TLOAD`), so the pattern is established.

2. **Extension-data identity**: Require the router to embed the originating user address in `extensionData`, and have the extension decode and verify it (with a signature or a factory-registered router whitelist).

Either approach must ensure the identity cannot be spoofed by an arbitrary caller.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  - Admin calls setAllowedToSwap(pool, router, true)      // router allowlisted for periphery support
  - bob is NOT on the allowlist

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  2. Router calls pool.swap(recipient=bob, ...)
  3. pool.swap() calls _beforeSwap(msg.sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] == true  → passes
  5. bob's swap executes at oracle price, draining LP funds

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [1](#0-0) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-40)
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
```
