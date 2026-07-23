### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the router contract, not the actual user. If the pool admin allowlists the router (the only way to permit router-mediated swaps for legitimate users), every unprivileged address can bypass the allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router is `msg.sender` of `pool.swap()`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin now faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every address on the network can bypass the allowlist by calling the public router |

Because `MetricOmmSimpleRouter` is a public, permissionless contract, allowlisting it is equivalent to setting `allowAllSwappers[pool] = true`. The guard is silently voided.

The analog to the ERC721 `_mint` / `_safeMint` class: just as `_mint` skips the recipient-capability check when an intermediary is involved, `SwapAllowlistExtension` skips the actual-user identity check when an intermediary router is involved. The configured guard exists but is misapplied to the wrong actor.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd addresses, specific market makers, or whitelisted counterparties can be freely accessed by any unprivileged address via the router. This breaks the core allowlist invariant and allows unauthorized swaps against restricted liquidity, which can drain LP assets or enable front-running in pools that were designed to be closed to the public.

**Severity: Medium** — broken core pool access-control with direct fund-impact potential; requires the pool admin to have allowlisted the router (a natural operational step).

---

### Likelihood Explanation

Any pool that uses `SwapAllowlistExtension` and also wants its allowlisted users to be able to use the standard router must allowlist the router. This is the expected operational path. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges.

---

### Recommendation

The extension must verify the **actual economic actor**, not the intermediary. Two complementary fixes:

1. **Pass the originating user through the router.** The router should forward the original `msg.sender` as an explicit `sender` field in `extensionData`, and the extension should decode and check that value instead of (or in addition to) the `sender` argument.

2. **Alternatively, gate on `recipient` or require the pool to expose an authenticated caller field.** The pool could store the original caller in transient storage before invoking extensions, giving extensions access to the true initiator regardless of intermediary.

Until fixed, pool admins should be warned that allowlisting the router negates the allowlist entirely.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin sets allowAllSwappers[pool] = false.
  - Pool admin sets allowedSwapper[pool][alice] = true  (alice is the only allowed swapper).
  - Pool admin sets allowedSwapper[pool][router] = true  (to let alice use the router).

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).
  - Router calls pool.swap(recipient=bob, ...) with msg.sender = router.
  - Pool calls _beforeSwap(sender=router, ...).
  - Extension checks: allowedSwapper[pool][router] == true  → passes.
  - Bob's swap executes against restricted pool liquidity.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; allowlist is bypassed.
``` [4](#0-3) [5](#0-4) [1](#0-0)

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
