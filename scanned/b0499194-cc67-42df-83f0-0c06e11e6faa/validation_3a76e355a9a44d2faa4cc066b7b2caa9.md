### Title
`DepositAllowlistExtension` Gates on `owner` (Share Recipient) Instead of `sender` (Token Payer), Allowing Unallowlisted Depositors to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` argument (the LP-share recipient) against the per-pool allowlist, but the token payer is `sender` (`msg.sender` of the pool call). Any unallowlisted address can bypass the deposit gate by calling `pool.addLiquidity(allowlistedAddress, ...)`, paying the tokens itself while routing the minted shares to an allowlisted address.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct identities to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

- `msg.sender` → `sender`: the address that will pay the tokens via the liquidity callback.
- `owner`: the address that will receive the minted LP shares.

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` entirely and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The contract's own NatSpec states the purpose is to gate `addLiquidity` **by depositor address**. The depositor — the entity that transfers tokens into the pool — is `sender`, not `owner`. Because `owner` is a free caller-supplied parameter with no constraint tying it to the actual payer, any unallowlisted address can pass an allowlisted address as `owner`, satisfy the check, and complete the deposit.

This is the direct analog of the external M-04 bug: a guard is configured and present in the hook chain, but it is bound to the wrong identity, so the intended protection is never applied to the actor it was meant to gate.

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., for regulatory compliance, KYC gating, or controlled LP composition). With this bug:

1. An unallowlisted address calls `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)`.
2. The extension checks `allowedDepositor[pool][allowlistedAddress]` → passes.
3. The unallowlisted address pays the tokens via the liquidity callback; the allowlisted address receives the LP shares.
4. The pool now holds liquidity sourced from an unallowlisted depositor, violating the admin-boundary the allowlist was meant to enforce.

This is an admin-boundary break: an unprivileged (unallowlisted) path bypasses a configured access-control guard, which falls within the contest's allowed impact gate.

---

### Likelihood Explanation

The trigger requires no special privilege. Any externally-owned address can call `pool.addLiquidity` directly with an arbitrary `owner`. The only prerequisite is knowing at least one allowlisted address for the target pool, which is publicly readable via `allowedDepositor` or observable on-chain. The bypass is therefore reachable by any actor at any time the pool is live.

---

### Recommendation

Change the `beforeAddLiquidity` hook to check `sender` (the first argument, the token payer) instead of `owner` (the second argument, the share recipient):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This ensures the guard is applied to the address that actually transfers tokens into the pool, matching the stated intent of gating by depositor address.

---

### Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` wired into `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` and `setAllowedToDeposit(pool, bob, false)`.
3. `bob` (unallowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `bob` pays the tokens via the liquidity callback; `alice` receives the LP shares.
6. `bob` has successfully deposited into the pool despite being explicitly excluded from the allowlist. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-14)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```
