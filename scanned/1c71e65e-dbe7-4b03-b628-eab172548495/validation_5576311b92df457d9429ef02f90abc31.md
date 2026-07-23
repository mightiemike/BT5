### Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual user, allowing any actor to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

The `SwapAllowlistExtension` is designed to restrict which addresses may swap on a curated pool. Its `beforeSwap` hook receives the `sender` argument forwarded by `ExtensionCalling._beforeSwap`, which is always `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The allowlist lookup is keyed by `(pool, sender)`, so it checks the router's address rather than the actual trader's address. If the pool admin allowlists the router (the only way to permit any router-mediated swap), every non-allowlisted user can bypass the curated gate by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInput(...)
         └─→ pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)
                  msg.sender = router
               └─→ ExtensionCalling._beforeSwap(msg.sender=router, recipient, ...)
                        └─→ SwapAllowlistExtension.beforeSwap(sender=router, ...)
                                 └─→ allowedSwapper[pool][router]  ← wrong actor checked
```

In `MetricOmmPool.swap`, `msg.sender` is passed directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` then forwards that `sender` verbatim to every configured extension: [2](#0-1) 

The `IMetricOmmExtensions.beforeSwap` interface signature confirms `sender` is the first argument the extension receives: [3](#0-2) 

The `SwapAllowlistExtension.beforeSwap` implementation keys its allowlist lookup on `(pool, sender)`. When the call originates from the router, `sender` is the router address. The pool admin must allowlist the router to permit any router-mediated swap at all; once the router is allowlisted, the gate is open to every user who routes through it.

This is structurally identical to the SEDA `call_result_write` bug: a guard that is supposed to apply a protection (metering / allowlist) is present in the code path but is applied to the wrong subject, making it trivially bypassable.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` is intended to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners). Once the router is allowlisted (required for any router-mediated flow), any address — including those explicitly excluded from the allowlist — can execute swaps against the pool's liquidity by calling `MetricOmmSimpleRouter.exactInput` or `exactOutput`. This constitutes a **complete bypass of the curation policy**, allowing unauthorized actors to drain pool liquidity at oracle-anchored prices, extract LP value, or front-run allowlisted participants. The impact is a direct loss of the pool's intended access-control invariant with immediate fund-impacting consequences for LPs who deposited under the assumption that only vetted counterparties could trade.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a first-party, publicly deployed periphery contract. Any user aware of the router can exploit this without any privileged access, special tokens, or multi-step setup. The only precondition is that the pool admin has allowlisted the router (which is the normal operational requirement to support routing). This is a **low-barrier, unprivileged, single-transaction exploit** reachable on every production pool that uses `SwapAllowlistExtension` with router support enabled.

---

### Recommendation

The `SwapAllowlistExtension.beforeSwap` hook must check the **originating user**, not the intermediary router. Two complementary fixes:

1. **Pass `tx.origin` or a user-supplied identity through `extensionData`**: The router should encode the actual caller in `extensionData`; the extension reads and verifies it. This requires the extension to trust the router's encoding, which is acceptable if the router is a known, immutable contract.

2. **Preferred — check `recipient` or a dedicated `user` field**: Redesign the `beforeSwap` interface to carry a separate `user` field (the economic beneficiary) distinct from `sender` (the contract initiating the call). The allowlist gates on `user`.

Until fixed, pools relying on `SwapAllowlistExtension` for access control should not allowlist the router, accepting that router-mediated swaps are unavailable for those pools.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Router is allowlisted so that allowedUser can use it.
// allowedSwapper[pool][router] = true
// allowedSwapper[pool][allowedUser] = true
// allowedSwapper[pool][attacker] = false  ← explicitly excluded

// Attacker bypasses the allowlist:
vm.prank(attacker);
router.exactInput(
    MetricOmmSimpleRouter.ExactInputParams({
        tokenIn: token0,
        tokenOut: token1,
        pool: curated_pool,
        recipient: attacker,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        callbackData: "",
        extensionData: ""
    })
);
// pool.swap is called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
// Attacker receives token1 output despite being explicitly excluded
```

The pool's `swap` receives `msg.sender = router`: [4](#0-3) 

The extension order dispatcher calls every configured before-swap extension with `sender = router`: [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
