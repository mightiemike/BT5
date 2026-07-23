### Title
SwapAllowlistExtension Checks Router Address Instead of Actual End User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool sets `sender = msg.sender = router`. The extension therefore checks whether the **router** is allowlisted, not the actual end user. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users inadvertently opens the gate to every user on the network.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every before-swap hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to each configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the value the pool forwarded — which is the **router address** for any router-mediated swap, not the human trader. The allowlist lookup therefore resolves to `allowedSwapper[pool][router]`.

A pool admin who wants allowlisted users to be able to trade through the public router must add the router to the allowlist. The moment the router is added, the check passes for **every caller** of the router, because the extension never sees the original `tx.origin` or the router's caller — only the router's own address.

---

### Impact Explanation

A curated pool (e.g., KYC-only, institutional-only, or whitelist-gated) that relies on `SwapAllowlistExtension` to restrict trading is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. The attacker receives pool output tokens at the oracle-derived price; the pool's LP providers bear the counterparty exposure that was supposed to be restricted to vetted counterparties. This is a direct loss of the curation guarantee and, depending on pool design, a direct loss of LP principal to unvetted counterparties.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router — a natural and expected action for any pool that wants to support the standard periphery swap path. The router is a public, permissionless contract. No special privilege, flash loan, or multi-step setup is needed; a single `exactInputSingle` call from any EOA suffices.

---

### Recommendation

The extension must gate on the **economic actor**, not the intermediary. Two complementary fixes:

1. **In `SwapAllowlistExtension`**: check `tx.origin` as a fallback when `sender` is a known router, or require the router to forward the original caller explicitly via `extensionData`.
2. **Preferred — in `MetricOmmSimpleRouter`**: encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it when the direct `sender` is a registered router. This keeps the extension stateless and avoids `tx.origin` risks.

---

### Proof of Concept

```
1. Pool admin deploys a pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // only Alice may swap
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router needed for router path
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient=bob, ...)
   → pool calls extension.beforeSwap(sender=router, ...)
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes for Bob
5. Bob receives pool output tokens; the allowlist is fully bypassed.
```

The pool admin faces an impossible choice: either allowlist the router (bypass for all users) or do not (router path broken for legitimate allowlisted users). There is no configuration that simultaneously supports router-mediated swaps and enforces per-user allowlist policy. [4](#0-3) [5](#0-4) [2](#0-1)

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
