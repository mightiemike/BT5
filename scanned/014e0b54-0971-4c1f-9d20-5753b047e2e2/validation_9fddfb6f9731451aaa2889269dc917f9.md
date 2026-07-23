Based on the code I was able to retrieve, here is the analysis:

---

### Title
SwapAllowlistExtension Gates Router Address Instead of End-User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the natural configuration to enable router-based swaps), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the public router.

### Finding Description
`SwapAllowlistExtension.beforeSwap` enforces access control as follows: [1](#0-0) 

The `sender` parameter is the address the pool passes as the first argument when it calls `_beforeSwap` through `ExtensionCalling._callExtensionsInOrder`: [2](#0-1) 

The pool derives `sender` from `msg.sender` of its own `swap` call (the pool does not take a caller-supplied `sender` parameter; it uses `msg.sender` at the pool boundary). When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router. The extension therefore evaluates:

```
allowedSwapper[pool][router]   // NOT allowedSwapper[pool][end_user]
```

A pool admin who wants to allow specific users to trade will add those users to `allowedSwapper`. To also allow those users to trade through the public router, the admin must add the router to `allowedSwapper`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` is `true` for every caller of the router — including addresses the admin explicitly never allowlisted. [3](#0-2) 

The `allowAllSwappers` flag has the same problem: setting it `true` to open the pool to all router users simultaneously opens it to every address that can call the router. [4](#0-3) 

### Impact Explanation
Any user excluded from the allowlist can execute swaps on a restricted pool by calling `MetricOmmSimpleRouter`. Pools that use the allowlist to restrict trading to specific market makers (to protect LPs from adverse selection at oracle prices) are fully bypassed. LP principal is at risk because the oracle-anchored pool will settle trades at the oracle price regardless of who the counterparty is; the allowlist is the only mechanism preventing harmful counterparties from trading.

### Likelihood Explanation
The router is a public, permissionless periphery contract. Any pool that enables router-based swaps for its allowlisted users must add the router to `allowedSwapper`, which simultaneously opens the pool to all users. The misconfiguration is not obvious because the admin's intent ("allow my users to use the router") and the effect ("allow everyone") are indistinguishable at the setter call site. The generate_scanned_questions audit pivot explicitly flags this path: [5](#0-4) 

### Recommendation
The extension must resolve the true end-user identity rather than the intermediary's address. Two options:

1. **Pass `msg.sender` of the router through as an explicit `sender` argument.** The router should forward `msg.sender` to the pool as a trusted `sender` field, and the pool should pass that value — not its own `msg.sender` — to extensions. This requires a pool-level change to accept and forward a caller-supplied sender.

2. **Check `msg.sender` of the pool's swap call AND the forwarded user address.** The extension can require that either the direct caller or the forwarded user is allowlisted, but this requires the router to supply the user address in `extensionData` and the extension to decode and verify it.

### Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` and adds only `alice` to `allowedSwapper[pool]`.
2. Admin also adds `MetricOmmSimpleRouter` to `allowedSwapper[pool]` so that `alice` can use the router UI.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
4. The router calls `pool.swap(...)`. Pool's `msg.sender` = router.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
6. `bob`'s swap executes successfully, bypassing the allowlist entirely. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L22-25)
```text
  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
