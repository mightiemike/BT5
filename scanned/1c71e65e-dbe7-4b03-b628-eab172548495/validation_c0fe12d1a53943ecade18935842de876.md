### Title
`DepositAllowlistExtension` gates on `owner` instead of `sender`, enabling allowlist bypass and blocking legitimate router-based deposits — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` enforces the deposit allowlist against the LP-position `owner` address rather than the `sender` (the actual caller of `addLiquidity`). This is structurally inconsistent with `SwapAllowlistExtension.beforeSwap`, which correctly gates on `sender`. The mismatch produces two fund-impacting failure modes: (1) an unauthorized `sender` can bypass the allowlist entirely by naming an allowlisted address as `owner`, and (2) a legitimately allowlisted `sender` (e.g., a periphery router) is silently blocked whenever it deposits into a position whose `owner` is not independently allowlisted.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` checks the first hook parameter (`sender`) and ignores the second (`recipient`): [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` does the opposite — it silently discards the first parameter (`sender`) and checks the second (`owner`): [2](#0-1) 

The pool's extension-calling layer passes both `sender` and `owner` as distinct values: [3](#0-2) 

Because `sender` (the address that called `addLiquidity`) and `owner` (the LP-position beneficiary, freely chosen by the caller) are independent, the guard evaluates the wrong identity.

**Bypass path**: An address not on the allowlist calls `addLiquidity(owner = allowlisted_address, ...)`. The extension reads `allowedDepositor[pool][allowlisted_address]` → `true` and permits the call. The unauthorized caller's tokens enter the pool under the allowlisted owner's position, circumventing the access control the pool admin intended.

**DoS path**: A pool admin allowlists a periphery router by its address (expecting `sender` to be checked). The router calls `addLiquidity(owner = end_user, ...)`. The extension reads `allowedDepositor[pool][end_user]` → `false` and reverts with `NotAllowedToDeposit`, permanently blocking all router-mediated deposits even though the router is allowlisted.

---

### Impact Explanation

- **Bypass**: Any unprivileged address can deposit into a pool that is supposed to be restricted, violating the pool admin's access-control invariant. Tokens from unauthorized depositors enter the pool's accounting, potentially diluting or manipulating liquidity distribution.
- **DoS**: If the pool admin allowlists a router (the natural `sender` in a periphery-based flow), every deposit routed through it is reverted. Core `addLiquidity` functionality becomes permanently unusable for the intended actor class without a full reconfiguration.

Both outcomes satisfy the allowed impact gate: admin-boundary break (unprivileged path bypasses pool admin's allowlist) and broken core pool functionality (unusable liquidity flow).

---

### Likelihood Explanation

- The bypass requires only knowing one allowlisted address (publicly readable from `allowedDepositor`) and calling `addLiquidity` with `owner` set to that address. No special privilege is needed.
- The DoS is triggered automatically whenever a router (allowlisted as `sender`) deposits on behalf of any user whose address is not separately allowlisted — a common periphery pattern.
- Both conditions are reachable by any unprivileged actor in normal protocol operation.

---

### Recommendation

Change `beforeAddLiquidity` to check `sender` (the first parameter) instead of `owner`, mirroring the pattern in `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is genuinely to gate on LP-position ownership (not on the caller), the contract's NatSpec and admin tooling must be updated to reflect that, and the `setAllowedToDeposit` documentation must clearly state that routers must allowlist every end-user `owner` individually.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` in the `beforeAddLiquidity` hook order.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) calls `pool.addLiquidity(owner=alice, salt=0, deltas=..., extensionData=...)` directly.
4. The pool calls `extension.beforeAddLiquidity(sender=bob, owner=alice, ...)`.
5. The extension evaluates `allowedDepositor[pool][alice]` → `true` and returns the success selector.
6. Bob's tokens are deposited into Alice's LP position. The allowlist is bypassed with zero privilege.

Conversely, if the pool admin instead calls `setAllowedToDeposit(pool, router, true)` and the router calls `pool.addLiquidity(owner=bob, ...)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```
