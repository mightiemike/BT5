### Title
`SwapAllowlistExtension` gates the router address instead of the real swapper, allowing any user to bypass the curated-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` as the first argument, which is `msg.sender` of the pool — i.e., the `MetricOmmSimpleRouter` contract when a user routes through it. If the router is allowlisted (as it must be for any legitimate user to use it), every user on the network can bypass the curated-pool allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every extension in the configured order: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` (analogous to `DepositAllowlistExtension.beforeAddLiquidity`) checks the `sender` argument against its per-pool allowlist. When a user calls `MetricOmmSimpleRouter.exact*`, the router is `msg.sender` of the pool, so `sender = router`. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

The `DepositAllowlistExtension` shows the exact keying pattern used across all periphery extensions — `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and the second key is the actor argument forwarded from the pool: [3](#0-2) 

The swap extension follows the same pattern but keys on `sender` (the router) rather than the originating user. The research notes in the repository explicitly identify this as the intended attack surface: [4](#0-3) 

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a set of trusted counterparties (e.g., KYC'd addresses or protocol-owned contracts). To allow those trusted users to use the public router, the admin must allowlist the router address. Once the router is allowlisted, **any** address can call `MetricOmmSimpleRouter.exact*` and the extension will see `sender = router` (allowed) rather than the real user (not allowed). The allowlist is completely bypassed. Unauthorized users can drain LP liquidity at oracle-derived prices, execute arbitrage the pool was designed to prevent, or interact with pools that were meant to be access-controlled.

---

### Likelihood Explanation

The trigger requires only a standard public call to `MetricOmmSimpleRouter`. No privileged access, flash loan, or special token behavior is needed. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can exploit this immediately. The router is a supported, documented periphery contract, so the admin is expected to allowlist it.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **originating user**, not on `sender`. Two options:

1. **Pass the real user through `extensionData`**: The router encodes the originating `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires the router to be trusted to supply honest data.

2. **Check `sender` only when it is not a known router, and require the router to forward the real user identity via a signed or callback-verified mechanism**: The extension maintains a registry of trusted routers and, for router calls, reads the real user from a router-provided field that the router cannot forge.

The `DepositAllowlistExtension` avoids this problem for deposits because it checks `owner` (the LP-share recipient, which the pool enforces via `removeLiquidity`'s `msg.sender != owner` guard). The swap extension has no equivalent on-chain binding between the economic actor and a pool-enforced field, so the fix must be explicit.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin allowlists: router=true, alice=true, bob=false

Attack (Bob bypasses allowlist):
  1. Bob calls MetricOmmSimpleRouter.exactInput(pool, ...)
  2. Router calls pool.swap(recipient=bob, ...)
     → msg.sender of pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling calls SwapAllowlistExtension.beforeSwap(sender=router, ...)
  5. Extension checks allowedSwapper[pool][router] → true → PASSES
  6. Swap executes; Bob receives output tokens from the curated pool

Expected: revert NotAllowedToSwap (bob is not allowlisted)
Actual:   swap succeeds because router is allowlisted and sender=router
```

The invariant broken: *a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.* [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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

**File:** generate_scanned_questions.py (L732-738)
```python
        Vector(
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
```
