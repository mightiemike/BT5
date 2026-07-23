### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` of the pool's `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-based swaps for their permitted users inadvertently opens the gate to every user, because the extension cannot distinguish which end user is behind the router call.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows: [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct key), and `sender` is the first argument the pool passes into `_beforeSwap`: [2](#0-1) 

The pool forwards whatever address it received as the caller of its own `swap` entry point. When `MetricOmmSimpleRouter` executes a swap, it calls `pool.swap(...)` directly, so the pool's `msg.sender` — and therefore the `sender` the extension sees — is the **router contract address**, not the end user. [3](#0-2) 

The extension's per-pool allowlist maps `pool → swapper → bool`: [4](#0-3) 

Because the router is a single shared contract, any entry `allowedSwapper[pool][router] = true` grants every user who calls the router the ability to pass the guard, regardless of whether that individual user is on the allowlist.

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., KYC-only, institutional-only) and allowlists the router to support the standard periphery UX has effectively disabled the allowlist for all router-mediated swaps. Any unprivileged user can call `MetricOmmSimpleRouter.exactInput` / `exactOutput` targeting the restricted pool and the extension will approve the call because `allowedSwapper[pool][router]` is `true`. The attacker receives output tokens from a pool they were never authorized to trade in, and the pool's LP providers bear the resulting exposure.

Conversely, if the admin does **not** allowlist the router, legitimately allowlisted users are silently blocked from using the router, breaking the core swap flow for the intended participants.

---

### Likelihood Explanation

The bypass requires no special privilege. Any externally-owned account can call `MetricOmmSimpleRouter` with the target pool address. The only precondition is that the pool admin has allowlisted the router (a natural operational step to support the standard UX), which makes the bypass reachable in every realistic deployment of a restricted pool that also supports router-based trading.

---

### Recommendation

The extension should check the **end user's identity**, not the intermediary's. Two complementary fixes:

1. **Pass the original caller through the router**: `MetricOmmSimpleRouter` should accept an explicit `sender` parameter (or use `msg.sender`) and forward it to the pool as a verified identity, with the pool recording it separately from `msg.sender`.
2. **Check `sender` correctly in the extension**: Until the pool surfaces the true originator, the extension should document that `sender` equals the direct pool caller and warn admins never to allowlist shared router contracts. A safer design is to check `tx.origin` as a secondary guard or require the router to pass a signed permit that the extension can verify.

The cleanest fix is for the pool's `swap` function to accept an explicit `swapper` address (verified via callback or signature) and pass that — not `msg.sender` — as `sender` to extensions.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls:
       extension.setAllowedToSwap(pool, userA, true)   // allowlist a specific user
       extension.setAllowedToSwap(pool, router, true)  // allowlist router so userA can use it
3. userB (NOT on the allowlist) calls:
       router.exactInput({ pool: pool, ... })
4. Router calls pool.swap(...) — pool's msg.sender = router.
5. Pool calls extension.beforeSwap(router, recipient, ...).
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. userB's swap executes successfully in the restricted pool.
8. userA's intended exclusivity is broken; any user can trade.
```

The `generate_scanned_questions.py` audit pivot for this path explicitly flags: *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
