### Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` — the `MetricOmmSimpleRouter` when a user routes through it, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user who routes through the same router, fully bypassing the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument. The pool passes its own `msg.sender` as that argument:

```solidity
// SwapAllowlistExtension.sol lines 31-41
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
``` [1](#0-0) 

Inside the extension, `msg.sender` is the pool (the pool calls the extension), and `sender` is whoever called the pool's `swap`. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router, so the extension evaluates:

```
allowedSwapper[pool][router]
```

not

```
allowedSwapper[pool][actual_user]
```

The pool's `swap` function passes `msg.sender` directly to `_beforeSwap` with no mechanism for the router to inject the originating user's address: [2](#0-1) 

The allowlist admin API only exposes `setAllowedToSwap(pool, swapper, allowed)` and `setAllowAllSwappers(pool, allowed)`, keyed by the address the extension will actually see as `sender`: [3](#0-2) 

---

### Impact Explanation

Two fund-impacting outcomes arise from this wrong-actor binding:

**Scenario A — Allowlist bypass (High):** The pool admin allowlists the router address so that their curated users can swap through the standard periphery path. Because the extension checks `allowedSwapper[pool][router]`, every user who routes through `MetricOmmSimpleRouter` passes the guard regardless of whether their own address is on the allowlist. The curation policy is completely nullified; any unprivileged user can trade on a pool that was intended to be restricted.

**Scenario B — Broken core functionality (Medium):** The pool admin does not allowlist the router. Allowlisted users who attempt to swap through the router have their transactions reverted (`NotAllowedToSwap`) even though their own address is on the allowlist. The only usable path is a direct `pool.swap` call, making the standard router unusable for any pool that deploys this extension.

Both outcomes are contest-relevant: Scenario A is a direct policy bypass enabling unauthorized trading on curated pools; Scenario B breaks the core swap flow for legitimate users.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will encounter one of the two failure modes above. The trigger requires no privileged access — any user can call the router's `exactInputSingle` or equivalent path. The pool admin's natural action of allowlisting the router to "enable router swaps" is precisely what opens Scenario A. [4](#0-3) 

---

### Recommendation

The pool's `swap` function should propagate the originating user's address through the extension call chain rather than its own `msg.sender`. One approach: add an explicit `originator` field to the swap parameters that the router populates with the end-user's address, and have `_beforeSwap` forward that field as `sender` to extensions. Alternatively, the extension can read the router's stored callback context (if the router exposes the current payer) to recover the true user identity. Until this is resolved, `SwapAllowlistExtension` cannot be safely combined with `MetricOmmSimpleRouter` on any pool where the allowlist is intended to restrict access.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice should be able to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully swaps on a pool he was never authorized to access.

The invariant broken: **every guard must key authorization to the same actor the economic action is attributed to.** The economic action belongs to Bob; the guard checks the router. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
```text
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
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L271-295)
```text
      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }

    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
```
