The call chain is fully traceable. Here is the complete analysis:

## Call Chain Trace

**Direct swap:**
```
User → MetricOmmPool::swap()
  msg.sender = User
  _beforeSwap(sender=User, ...)
  → SwapAllowlistExtension::beforeSwap(sender=User)
  → allowedSwapper[pool][User]  ✓ correct
```

**Router swap:**
```
User → MetricOmmSimpleRouter::exactInputSingle()
  → IMetricOmmPoolActions(pool).swap(recipient, ...)
    msg.sender = Router
    _beforeSwap(sender=Router, ...)
    → SwapAllowlistExtension::beforeSwap(sender=Router)
    → allowedSwapper[pool][Router]  ✗ wrong identity
```

The pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling::_beforeSwap` forwards that `sender` verbatim to the extension: [2](#0-1) 

The hook then checks `allowedSwapper[msg.sender=pool][sender]`: [3](#0-2) 

When the router calls the pool, `sender` is the router's address, not the original user's address.

---

### Title
Router-Mediated Swaps Pass Router Address as Sender to `SwapAllowlistExtension`, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate caller of `MetricOmmPool::swap`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the original user's address. This breaks the allowlist invariant in both directions.

### Finding Description
The `SwapAllowlistExtension` is designed to restrict swaps to a per-pool allowlist of addresses. The `sender` parameter it receives is the `msg.sender` of the pool's `swap()` call. [4](#0-3) 

When `MetricOmmSimpleRouter::exactInputSingle` (or any `exact*` variant) calls the pool, it passes no user identity — the pool sees the router as `msg.sender`: [5](#0-4) 

This produces two concrete failure modes:

**Failure mode A — Allowlist bypass (unauthorized access):**
A pool admin who wants to allow router-mediated swaps must allowlist the router address. Once `allowedSwapper[pool][router] = true`, *any* user — including non-allowlisted ones — can bypass the allowlist by calling the router. The hook cannot distinguish between different users routing through the same router contract.

**Failure mode B — Allowlisted users locked out of the router:**
If the pool admin does not allowlist the router (e.g., they only allowlisted specific EOAs), then every router-mediated swap by an allowlisted user reverts with `NotAllowedToSwap`, because the hook sees `sender = router`, which is not in the allowlist.

Neither failure mode requires any crafted `extensionData` — the `extensionData` parameter is not read by this hook at all. [3](#0-2) 

### Impact Explanation
Failure mode A is the high-impact path: a pool configured to restrict swaps to KYC'd or permissioned addresses is fully bypassed by any user who routes through the public router. The allowlist provides no protection against router-mediated swaps. Unauthorized users can execute swaps in a pool that was designed to be access-controlled, which constitutes broken core pool functionality and potential unauthorized fund movement.

### Likelihood Explanation
Any pool that uses `SwapAllowlistExtension` and also wants to support the official router must allowlist the router, triggering failure mode A. This is the natural and expected configuration for a permissioned pool that still wants to be accessible via the standard UI/router. The bypass requires no special privileges, no crafted calldata, and no multi-block timing — a single router call suffices.

### Recommendation
The hook must verify the *original* user, not the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the hook decodes and verifies it. This requires the router to be trusted (i.e., the hook must also verify `sender == router`).
2. **Check `recipient` instead of `sender`**: If the pool design guarantees that `recipient` is always the end user, the hook can gate on `recipient`. However, this changes the semantics of the allowlist.
3. **Dedicated router allowlist**: Maintain a separate mapping for trusted routers and, when `sender` is a trusted router, require the hook to also check the decoded user from `extensionData`.

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)  // to enable router swaps
  - Pool admin does NOT allowlist attacker EOA

Attack:
  - Attacker calls MetricOmmSimpleRouter::exactInputSingle(pool, ...)
  - Router calls pool.swap(recipient=attacker, ...)
  - Pool calls _beforeSwap(sender=router, ...)
  - Hook checks allowedSwapper[pool][router] == true  → PASSES
  - Attacker's swap executes despite not being on the allowlist

Assert: attacker (non-allowlisted EOA) successfully swapped in a restricted pool.
```

The "velocity-envelope" and "per-block price-change cap" framing in the question title does not correspond to any logic present in `SwapAllowlistExtension` — the extension contains no price or block-timing checks. The real and provable vulnerability is the identity confusion described above.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
