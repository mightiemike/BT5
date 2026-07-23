Based on my investigation of the codebase structure, `ExtensionCalling.sol`, `IMetricOmmExtensions.sol`, and the research pivot hints in `generate_scanned_questions.py`, here is the analog finding:

---

### Title
SwapAllowlistExtension Checks Router Address as `sender`, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
The `SwapAllowlistExtension.beforeSwap` hook receives `sender` as the address that called `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the originating user. If the router is present on the allowlist (or if the pool admin adds it to enable normal routing), every non-allowlisted user can bypass the swap restriction by simply calling the public router instead of the pool directly.

### Finding Description

`ExtensionCalling._beforeSwap` encodes and forwards `sender` — which is `msg.sender` of the pool's `swap` call — to every configured extension: [1](#0-0) 

The `IMetricOmmExtensions.beforeSwap` interface receives this `sender` as its first argument: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (i.e., the direct caller of `pool.swap`) is on the per-pool allowlist. When a user calls `MetricOmmSimpleRouter.exactInput` or any `exact*` entry point, the router becomes `msg.sender` of `pool.swap`, so `sender` = router address. The extension sees the router, not the originating user. [3](#0-2) 

The research pivot confirms this exact concern:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

### Impact Explanation
A pool admin deploys a `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd traders or protocol-controlled addresses). The admin also adds `MetricOmmSimpleRouter` to the allowlist so that allowlisted users can route multi-hop swaps. Any non-allowlisted user can now call `MetricOmmSimpleRouter.exactInput` targeting the restricted pool; the hook sees `sender = router`, which is allowlisted, and permits the swap. The allowlist guard is fully bypassed. This constitutes an **admin-boundary break**: an unprivileged path (the public router) circumvents a pool-level access control that was intended to restrict who can trade, with direct fund-flow consequences (non-permitted parties execute swaps against the pool's liquidity).

### Likelihood Explanation
- `MetricOmmSimpleRouter` is a public, permissionless contract.
- Any user can call it with any pool address and any swap parameters.
- The only precondition is that the router is on the allowlist, which is the natural setup any admin would choose to allow their own allowlisted users to use the router.
- No privileged access, no special tokens, no malicious setup required — a standard user interaction triggers the bypass.

### Recommendation
The `SwapAllowlistExtension.beforeSwap` hook should gate on the **originating user**, not on `sender`. Two options:

1. **Pass `tx.origin` or a forwarded caller field**: Extend the extension interface or use `extensionData` to carry the true initiating address, and check that instead of `sender`.
2. **Check both `sender` and a forwarded identity**: Require that `extensionData` contains the real caller's address (signed or forwarded by the router), and verify it against the allowlist.
3. **Restrict the router itself**: Do not allowlist the router; require allowlisted users to call the pool directly. Document this constraint clearly.

Option 1 or 2 is preferred because option 3 breaks multi-hop routing for legitimate users.

### Proof of Concept

1. Pool admin deploys pool with `SwapAllowlistExtension` configured as `BEFORE_SWAP_ORDER` extension.
2. Admin calls `allowedSwapper[pool][routerAddress] = true` and `allowedSwapper[pool][aliceAddress] = true`. Bob (non-allowlisted) is excluded.
3. Bob calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(recipient=Bob, ...)` — `msg.sender` of pool = router.
5. Pool calls `_beforeSwap(sender=router, ...)` → `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → hook returns success selector.
7. Swap executes. Bob, who was explicitly excluded from the allowlist, has successfully swapped against the restricted pool. [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
