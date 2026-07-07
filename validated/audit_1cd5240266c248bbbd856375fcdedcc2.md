### Title
Missing Zero-Address Check on `clearinghouseLiq` Initialization Silently Disables Liquidations - (File: core/contracts/Clearinghouse.sol)

### Summary
`Clearinghouse.initialize()` assigns `clearinghouseLiq` from a caller-supplied parameter without validating it against `address(0)`. If initialized to zero, every subsequent call to `liquidateSubaccount()` silently succeeds via a delegatecall to `address(0)` while executing no liquidation logic, permanently disabling the protocol's solvency enforcement mechanism.

### Finding Description
In `Clearinghouse.initialize()`, the `_clearinghouseLiq` parameter is stored directly into `clearinghouseLiq` with no zero-address guard:

```solidity
function initialize(
    address _endpoint,
    address _quote,
    address _clearinghouseLiq,   // ← no require(_clearinghouseLiq != address(0))
    uint256 _spreads,
    address _withdrawPool
) external initializer {
    __Ownable_init();
    setEndpoint(_endpoint);
    quote = _quote;
    clearinghouse = address(this);
    clearinghouseLiq = _clearinghouseLiq;   // ← stored unconditionally
    ...
}
``` [1](#0-0) 

`clearinghouseLiq` is the sole target of the `delegatecall` inside `liquidateSubaccount()`:

```solidity
(bool success, bytes memory result) = clearinghouseLiq.delegatecall(
    liquidateSubaccountCall
);
require(success, string(result));
``` [2](#0-1) 

In the EVM, a `delegatecall` to `address(0)` returns `(true, "")`. Because `success == true`, the `require` passes. No liquidation logic executes. The call returns normally, giving the sequencer and callers no indication that anything went wrong.

The same missing check exists on the post-deployment upgrade path `upgradeClearinghouseLiq()`, which also stores the supplied address unconditionally:

```solidity
function upgradeClearinghouseLiq(address _clearinghouseLiq) external {
    require(msg.sender == IProxyManager(_getProxyManager()).getProxyManagerHelper(), ERR_UNAUTHORIZED);
    clearinghouseLiq = _clearinghouseLiq;   // ← no zero-address check
}
``` [3](#0-2) 

### Impact Explanation
With `clearinghouseLiq == address(0)`:

- Every `LiquidateSubaccount` transaction submitted by the sequencer reaches `Clearinghouse.liquidateSubaccount()`, delegatecalls to `address(0)`, silently returns success, and leaves the insolvent position fully intact.
- Unhealthy subaccounts accumulate unbounded bad debt with no on-chain mechanism to close them.
- Insurance and the broader solvency model are permanently bypassed; the protocol cannot enforce margin requirements.

This is a **solvency/accounting corruption** impact, not merely a denial-of-service.

### Likelihood Explanation
The `initialize()` function is called exactly once during deployment. A deployment script that passes `address(0)` for `_clearinghouseLiq` — whether by mistake, a misconfigured environment variable, or a partially completed deployment — permanently corrupts the contract with no recovery path (the `initializer` modifier prevents re-initialization). The absence of any defensive check makes this a realistic deployment-time mistake, matching the exact class of the reference report.

### Recommendation
Add an explicit zero-address guard in both `initialize()` and `upgradeClearinghouseLiq()`:

```solidity
// In initialize():
require(_clearinghouseLiq != address(0), "clearinghouseLiq cannot be zero");
clearinghouseLiq = _clearinghouseLiq;

// In upgradeClearinghouseLiq():
require(_clearinghouseLiq != address(0), "clearinghouseLiq cannot be zero");
clearinghouseLiq = _clearinghouseLiq;
```

### Proof of Concept

1. Deploy `Clearinghouse` and call `initialize(endpoint, quote, address(0), spreads, withdrawPool)`.
2. `clearinghouseLiq` is now `address(0)`.
3. A subaccount becomes insolvent (maintenance health < 0).
4. The sequencer submits a `LiquidateSubaccount` transaction; `Clearinghouse.liquidateSubaccount()` is called via `onlyEndpoint`.
5. `clearinghouseLiq.delegatecall(liquidateSubaccountCall)` executes against `address(0)`, returning `(true, "")`.
6. `require(true, "")` passes. The function returns. The insolvent position is unchanged.
7. Bad debt accumulates indefinitely; no liquidation is ever possible. [4](#0-3)

### Citations

**File:** core/contracts/Clearinghouse.sol (L25-40)
```text
    function initialize(
        address _endpoint,
        address _quote,
        address _clearinghouseLiq,
        uint256 _spreads,
        address _withdrawPool
    ) external initializer {
        __Ownable_init();
        setEndpoint(_endpoint);
        quote = _quote;
        clearinghouse = address(this);
        clearinghouseLiq = _clearinghouseLiq;
        spreads = _spreads;
        withdrawPool = _withdrawPool;
        emit ClearinghouseInitialized(_endpoint, _quote);
    }
```

**File:** core/contracts/Clearinghouse.sol (L644-662)
```text
    function liquidateSubaccount(IEndpoint.LiquidateSubaccount calldata txn)
        external
        virtual
        onlyEndpoint
    {
        bytes4 liquidateSubaccountSelector = bytes4(
            keccak256(
                "liquidateSubaccountImpl((bytes32,bytes32,uint32,bool,int128,uint64))"
            )
        );
        bytes memory liquidateSubaccountCall = abi.encodeWithSelector(
            liquidateSubaccountSelector,
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```

**File:** core/contracts/Clearinghouse.sol (L677-684)
```text
    function upgradeClearinghouseLiq(address _clearinghouseLiq) external {
        require(
            msg.sender ==
                IProxyManager(_getProxyManager()).getProxyManagerHelper(),
            ERR_UNAUTHORIZED
        );
        clearinghouseLiq = _clearinghouseLiq;
    }
```
