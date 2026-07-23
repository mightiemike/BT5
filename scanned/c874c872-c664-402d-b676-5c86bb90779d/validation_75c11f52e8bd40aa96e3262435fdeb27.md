### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end-user's address. If the pool admin allowlists the router (a natural configuration to enable router-based swaps), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`, which in turn passes it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap(...)` on the user's behalf. Inside the pool, `msg.sender` is the **router**, so `sender` delivered to the extension is the **router address**, not the end-user. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`.

A pool admin who wants to support router-based swaps must allowlist the router. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, including addresses that were never individually allowlisted. The allowlist is silently reduced to a binary "router allowed / router blocked" flag, defeating its per-user curation purpose entirely.

The `DepositAllowlistExtension` does **not** share this flaw: its `beforeAddLiquidity` gates the `owner` argument (the position owner), which is preserved correctly even when the liquidity adder is the `msg.sender`. [4](#0-3) 

---

### Impact Explanation

Any user can trade in a pool that was configured to restrict swaps to a curated set of addresses. The allowlist — the sole mechanism preventing unauthorized access to a curated pool — is fully bypassed. Unauthorized swaps drain LP positions and collect fees from liquidity that was deposited under the assumption that only vetted counterparties would trade against it. This is a direct loss of LP principal and a broken core pool invariant.

---

### Likelihood Explanation

The trigger condition is that the pool admin has allowlisted the router address. This is the expected configuration for any curated pool that also wants to support the standard periphery UX. The admin has no indication that allowlisting the router opens the gate to all users; the allowlist UI and events (`AllowedToSwapSet`) give no warning. The exploit requires no privileged keys, no special tokens, and no flash loans — only a call to the public router.

---

### Recommendation

The extension must resolve the true end-user identity rather than trusting the `sender` argument when the caller is a known intermediary. Two sound approaches:

1. **Pass-through identity**: Require the router to forward the original `msg.sender` as an explicit parameter in `extensionData`, and have the extension decode and check that value instead of `sender`.
2. **Recipient-based check**: Gate on `recipient` (the second argument to `beforeSwap`) rather than `sender` when the pool is configured to use the router, since the recipient is the economic beneficiary of the swap.
3. **Router-aware allowlist**: The extension can maintain a separate registry of trusted intermediaries and, when `sender` is a known intermediary, require that the decoded end-user from `extensionData` is individually allowlisted.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-based swaps
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not allowlisted

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInput(pool, ..., recipient=alice, ...)
  2. Router calls pool.swap(recipient=alice, ..., msg.sender=router)
  3. Pool calls _beforeSwap(sender=router, recipient=alice, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  → passes
  5. Swap executes; alice receives output tokens from the curated pool.

Expected: revert NotAllowedToSwap (alice is not allowlisted)
Actual:   swap succeeds because the router is allowlisted and sender == router
``` [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
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
