### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender` — the immediate caller of `pool.swap()` — against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's. A pool admin who allowlists the router (the natural step to let their approved users trade via the router) inadvertently opens the pool to every router user, bypassing the per-user allowlist entirely.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the value the pool passes as the originator of the swap. The pool sets `sender = msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` executes a swap on behalf of a user, it calls `pool.swap(...)` directly, making `msg.sender` — and therefore `sender` seen by the extension — the **router address**, not the end user. [2](#0-1) 

The extension does not inspect `extensionData` and has no other mechanism to recover the true initiating user. The `DepositAllowlistExtension`, by contrast, correctly gates on `owner` (the LP position owner, the economically relevant actor), not on `sender`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [3](#0-2) 

This asymmetry reveals that the swap allowlist was intended to gate the end user but is implemented to gate the immediate caller.

**Bypass scenario:**

| Configuration | Direct pool call | Router call |
|---|---|---|
| User A allowlisted, router NOT allowlisted | ✓ allowed | ✗ blocked (router not listed) |
| User A allowlisted, router allowlisted | ✓ allowed | ✓ allowed |
| User C (not allowlisted), router allowlisted | ✗ blocked | **✓ allowed — bypass** |

A pool admin who wants allowlisted users to trade via the router must allowlist the router. Doing so silently grants every router user the same access, defeating the allowlist entirely.

---

### Impact Explanation

A curated pool (e.g., KYC-gated, institutional-only, or restricted-strategy pool) relies on the swap allowlist to control who can trade. Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactOutputSingle` and execute swaps on the pool. This exposes LPs to:

- **Adverse selection** from uninvited counterparties trading against the oracle price.
- **Violation of curation policy** (regulatory, contractual, or risk-management constraints).
- **Direct LP principal loss** if the pool's spread/fee parameters were calibrated for a known, trusted counterparty set.

This matches the allowed impact gate: *"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."*

---

### Likelihood Explanation

The bypass is triggered by a routine, expected administrative action. Any pool admin who:
1. Deploys a pool with `SwapAllowlistExtension` to restrict trading to specific users, **and**
2. Allowlists the router so those users can trade via the standard periphery

will unknowingly open the pool to all router users. This is the natural and documented usage pattern for periphery-integrated pools. No attacker privilege is required beyond calling the public router.

---

### Recommendation

Gate on the true initiating user rather than the immediate caller. Two complementary approaches:

1. **Pass the original caller through `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` (the end user) into `extensionData` and have `SwapAllowlistExtension` decode and verify it. This requires a convention between router and extension.

2. **Check `recipient` as a proxy** (partial): For direct-user flows, `recipient` often equals the user. This is imperfect but better than checking the router.

3. **Document the limitation explicitly**: If the design intent is to gate the immediate caller only, the `SwapAllowlistExtension` NatSpec and admin tooling must warn that allowlisting the router opens the pool to all router users.

The deposit allowlist's pattern of checking `owner` (the economically relevant actor) should be the model for the swap allowlist.

---

### Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  admin allowlists user A:  E.setAllowedToSwap(P, userA, true)
  admin allowlists router R: E.setAllowedToSwap(P, router, true)
    (necessary so userA can trade via the router)

Attack (by userC, not allowlisted):
  userC calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  Router calls P.swap(recipient=userC, ...)
  Pool calls E.beforeSwap(sender=router, ...)
  Extension checks: allowedSwapper[P][router] == true  → passes
  Swap executes for userC despite userC not being on the allowlist.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [4](#0-3) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
