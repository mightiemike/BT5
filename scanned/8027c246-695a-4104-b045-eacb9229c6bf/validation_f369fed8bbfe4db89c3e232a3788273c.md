### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the user. If the pool admin allowlists the router address (the only way to permit router-mediated swaps for their intended users), every unprivileged user can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)` — so `msg.sender` of `pool.swap()` is the router, not the user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted.

The pool admin faces an impossible choice:
- **Allowlist only individual user addresses** → allowlisted users cannot use the router at all (router is blocked).
- **Allowlist the router address** → every unprivileged user can bypass the per-user allowlist by routing through the public router.
- **Allowlist both individual users and the router** → same bypass as above.

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

The `generate_scanned_questions.py` audit target explicitly identifies this concern: [4](#0-3) 

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institutional-only, or regulatory-restricted) deploys `SwapAllowlistExtension` to restrict who may trade. The pool admin allowlists the router so that their approved users can access the pool through the standard periphery. Any non-allowlisted user then calls `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool. The extension sees `sender = router` (allowlisted), passes the check, and the unauthorized swap executes. The pool's LPs are exposed to toxic flow, uninvited counterparties, and potential regulatory liability — all of which the allowlist was deployed to prevent. This is a direct loss-of-protection impact on LP principal and pool integrity.

---

### Likelihood Explanation

Medium-High. The pool admin **must** allowlist the router to allow their approved users to use the standard periphery. This is the natural and expected operational configuration for any pool that intends to support router-mediated swaps. The bypass is then reachable by any unprivileged user with no special privileges, no malicious setup, and no non-standard tokens.

---

### Recommendation

The extension must gate on the **actual economic actor**, not the intermediary. Two sound approaches:

1. **Forward the original caller in `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. The pool's `_beforeSwap` already forwards `extensionData` unmodified to extensions.

2. **Check `recipient` as a proxy**: For direct swaps `recipient ≈ user`; for router swaps the router sets `recipient` to the user. This is imperfect (recipient can differ from swapper) but closer than checking `sender`.

The cleanest fix is option 1: standardize a convention where periphery contracts prepend the originating user address to `extensionData`, and the allowlist extension decodes it when present.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow their approved users to use MetricOmmSimpleRouter).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, ..., recipient=attacker, ...)
  2. Router calls pool.swap(recipient=attacker, ...) with msg.sender = router.
  3. pool._beforeSwap(sender=router, ...) is called.
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Result:
  Non-allowlisted attacker successfully swaps on a curated pool,
  bypassing the per-user allowlist entirely.
``` [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
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
