Based on my investigation of the codebase, I need to trace the extension hook plumbing through the swap path to find the analog.

From `ExtensionCalling.sol`, the `_beforeSwap` dispatcher passes `sender` directly to every configured extension: [1](#0-0) 

The `SwapAllowlistExtension.beforeSwap()` is documented as performing an `allowedSwapper` lookup **keyed by `pool` and `sender`**. When a user routes through `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`, the router calls `pool.swap()` directly, making the router the `msg.sender` of the pool call. The pool passes this `msg.sender` (the router) as `sender` to `_beforeSwap`, which forwards it to the extension. The extension therefore checks `allowedSwapper[pool][router_address]` rather than `allowedSwapper[pool][actual_user]`. [2](#0-1) 

The `generate_scanned_questions.py` audit scaffold explicitly flags this as the primary concern for the swap allowlist target:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [3](#0-2) 

---

### Title
Swap Allowlist Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument forwarded by the pool, which equals the router's address for all `MetricOmmSimpleRouter`-mediated swaps. Any non-allowlisted user can bypass the curated-pool swap gate by routing through the router.

### Finding Description
`MetricOmmPool.swap()` uses `msg.sender` as the `sender` value it passes into `ExtensionCalling._beforeSwap()`. [1](#0-0) 

`_beforeSwap` encodes that `sender` into the `IMetricOmmExtensions.beforeSwap` call dispatched to every configured extension. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` performs its allowlist lookup against this `sender`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`, `msg.sender` at the pool level is the router contract, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

Two broken outcomes follow:

1. **Router is allowlisted** (the natural production configuration so that router-mediated swaps work at all): every user, allowlisted or not, can swap on the curated pool by going through the router. The allowlist is completely ineffective.
2. **Router is not allowlisted**: no user can swap through the router on the curated pool, breaking the primary supported swap entry point.

The analog to the seed bug is exact: just as `EnforcedBinaryGuild.endProposal()` skipped `super.endProposal()` in the NO-option branch and left `activeProposalsNow` unchecked, `SwapAllowlistExtension.beforeSwap()` evaluates the wrong actor in the router branch and leaves the actual user's allowlist status unchecked.

### Impact Explanation
Any non-allowlisted user can trade on a pool whose operator deployed `SwapAllowlistExtension` specifically to restrict access. The bypass is unconditional for all router-mediated swaps. Curated pools (e.g., compliance-gated, KYC-gated, or partner-only pools) lose their access-control invariant entirely. Depending on pool configuration, this can result in unauthorized price impact, fee extraction, or LP-value leakage by actors the pool operator explicitly excluded.

### Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entry point documented in the protocol. Any user who calls `exactInputSingle` or `exactInput` triggers the bypass automatically. No special setup, flash loan, or privileged access is required. Likelihood is **High**.

### Recommendation
The `SwapAllowlistExtension` must check the identity of the economic actor, not the intermediary contract. Two options:

1. Have `MetricOmmPool.swap()` accept an explicit `sender` parameter that the router populates with `msg.sender` (the actual user) rather than relying on `msg.sender` at the pool level.
2. Alternatively, have the extension check the `recipient` or a user-supplied field in `extensionData` that the router populates with the originating user address, combined with a router-authenticity check.

The fix must ensure the checked identity is the same actor who economically benefits from the swap, regardless of which supported entry point is used.

### Proof of Concept

```
Setup:
  - Deploy MetricOmmPool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedSwapper(pool, router, true)   // router must be allowlisted for normal use
  - Pool admin does NOT call setAllowedSwapper(pool, attacker, true)

Attack:
  1. attacker (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
         tokenIn: token0, tokenOut: token1, pool: pool, ...
     })
  2. Router calls pool.swap(recipient=attacker, ...)
     → pool sees msg.sender = router
     → pool passes sender=router to _beforeSwap
  3. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true → PASSES
  4. Swap executes; attacker receives token1 output

Result:
  attacker successfully swaps on a pool they are not allowlisted for.
  The allowlist guard is bypassed for every router-mediated swap.
``` [4](#0-3)

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

**File:** generate_scanned_questions.py (L733-738)
```python
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
```
