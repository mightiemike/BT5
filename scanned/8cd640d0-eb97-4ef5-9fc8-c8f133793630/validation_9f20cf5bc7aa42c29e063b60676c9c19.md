### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `swap()` function passes `msg.sender` (the router contract) as `sender` to the extension hook. The extension therefore checks whether the **router** is allowlisted, not whether the **actual end user** is allowlisted. If the router is added to the allowlist (which is required for any router-mediated swap to succeed), every user — including those explicitly excluded — can bypass the allowlist by routing through the router.

---

### Finding Description

**Root cause — wrong identity bound to the guard parameter:**

In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap` forwards this value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `sender` against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The forced dilemma for pool admins:**

| Router allowlist state | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert, including those from legitimately allowlisted users |
| Router **allowlisted** | Every user — including those explicitly excluded — can bypass the allowlist through the router |

There is no configuration that simultaneously allows legitimate users to swap via the router and blocks non-allowlisted users.

**Analog to the external bug:** Just as RSKJ's `createContract` used `getCallValue()` (the current transaction's value) instead of the `endowment` parameter (the value explicitly supplied to `CREATE`), `SwapAllowlistExtension` uses the intermediary contract's address (the router, i.e., the "current caller") instead of the actual end-user address (the value the pool admin intended to gate on). [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, whitelisted market makers, or a private LP pool) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-derived prices against the pool's liquidity, extracting value from LPs who believed their pool was access-controlled. This breaks the **Broken core pool functionality** and **Admin-boundary break** impact categories: the allowlist guard — a configured security boundary — is rendered ineffective by a public, unprivileged path.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps.
- Any pool admin who deploys a `SwapAllowlistExtension` and also wants router users to be able to swap must allowlist the router, which immediately opens the bypass to all users.
- No special privileges, malicious setup, or non-standard tokens are required. Any EOA can call `MetricOmmSimpleRouter` directly. [5](#0-4) 

---

### Recommendation

The pool's `swap()` function should accept an explicit `swapper` parameter (the actual end user) that is distinct from `msg.sender` (the router/caller). The extension hook should receive this `swapper` address as the identity to gate. Alternatively, `SwapAllowlistExtension.beforeSwap()` should read the actual user from a trusted forwarding mechanism (e.g., ERC-2771 `_msgSender()` or a router-provided calldata field) rather than relying on the `sender` argument, which is the router when called indirectly. [3](#0-2) 

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, router, true)   // must do this for any router swap to work
  3. Pool admin calls setAllowedToSwap(pool, alice, false)   // alice is explicitly excluded

Attack:
  4. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  5. Router calls pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)
     → msg.sender = router
  6. Pool calls _beforeSwap(msg.sender=router, recipient=alice, ...)
  7. SwapAllowlistExtension.beforeSwap(sender=router, ...) checks:
       allowedSwapper[pool][router] == true  ✓  → swap proceeds
  8. Alice's swap executes at oracle price against pool liquidity.
     The allowlist guard was never consulted for Alice's identity.
``` [3](#0-2) [1](#0-0)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-41)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
