### Title
Unprotected `initialize` in `Clearinghouse` Allows Front-Running to Seize Ownership and Redirect All User Withdrawals — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.initialize` imposes no caller restriction. Any unprivileged address that calls it first becomes the contract owner and supplies all critical protocol addresses — including `withdrawPool`, `clearinghouseLiq`, and `endpoint` — with attacker-controlled values. This is a direct analog to the gorples-ido/gorples-core front-run initializer class: same missing guard, same ownership seizure, same asset-diversion path.

---

### Finding Description

`Clearinghouse.initialize` is `external` and guarded only by OpenZeppelin's `initializer` modifier, which prevents re-entry but places no restriction on *who* may call it first:

```solidity
function initialize(
    address _endpoint,
    address _quote,
    address _clearinghouseLiq,
    uint256 _spreads,
    address _withdrawPool
) external initializer {
    __Ownable_init();          // msg.sender becomes owner
    setEndpoint(_endpoint);
    quote = _quote;
    clearinghouse = address(this);
    clearinghouseLiq = _clearinghouseLiq;
    spreads = _spreads;
    withdrawPool = _withdrawPool;
    emit ClearinghouseInitialized(_endpoint, _quote);
}
``` [1](#0-0) 

The deployment flow for an upgradeable proxy is: (1) deploy implementation, (2) deploy proxy, (3) call `initialize` on the proxy. Between steps 2 and 3 there is an open window. Unlike `ContractOwner.initialize`, which explicitly checks `require(_deployer == msg.sender, "expected deployed to initialize")`, `Clearinghouse.initialize` has no equivalent guard. [2](#0-1) 

Additionally, the `Clearinghouse` contract defines no constructor calling `_disableInitializers()`, so the bare implementation address is also directly initializable — unlike `Verifier`, `Airdrop`, `BaseWithdrawPool`, `BaseProxyManager`, and `ContractOwner`, all of which protect their implementations with `_disableInitializers()`. [3](#0-2) 

The same pattern is present in `Endpoint.initialize` (sets `sequencer`, `clearinghouse`, `verifier`, `offchainExchange`) and `OffchainExchange.initialize` (sets `clearinghouse`, `endpoint`), neither of which has a caller check or a `_disableInitializers()` constructor. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

An attacker who wins the initialization race on `Clearinghouse`:

1. **Becomes the owner** via `__Ownable_init()` — gains permanent access to `addEngine`, `setWithdrawPool`, `setSpreads`, and all `onlyOwner` functions.
2. **Controls `withdrawPool`** — every user withdrawal flows through `handleWithdrawTransfer`, which calls `BaseWithdrawPool.submitWithdrawal` on the attacker-supplied address. All collateral withdrawals are redirected to the attacker.
3. **Controls `clearinghouseLiq`** — the liquidation implementation used in `delegatecall` inside `liquidateSubaccount` is attacker-supplied, enabling arbitrary code execution in the Clearinghouse's storage context.
4. **Controls `endpoint`** — the `onlyEndpoint` modifier throughout Clearinghouse trusts only the registered endpoint address; the attacker sets this to their own contract, blocking or hijacking all sequencer-submitted transactions. [6](#0-5) [7](#0-6) 

The corrupted state delta is: `owner`, `withdrawPool`, `clearinghouseLiq`, `endpoint`, and `quote` — all written to proxy storage in a single transaction before the legitimate deployer can act.

---

### Likelihood Explanation

The attack requires only mempool monitoring and a higher-gas front-run of the deployer's `initialize` call. No special privilege, no leaked key, no governance capture. The attacker needs to observe the proxy deployment transaction and submit their own `initialize` call before the deployer's call is mined. This is a well-understood MEV technique on EVM chains. The Ink Chain deployment context does not eliminate this window.

---

### Recommendation

Add a deployer check identical to the one already present in `ContractOwner`:

```solidity
// In Clearinghouse.initialize:
require(msg.sender == _expectedDeployer, "only deployer can initialize");
```

Or, preferably, add a `_disableInitializers()` constructor to `Clearinghouse`, `Endpoint`, and `OffchainExchange` (matching the pattern already used in `Verifier`, `Airdrop`, `BaseWithdrawPool`, `BaseProxyManager`, and `ContractOwner`), and use an atomic deploy-and-initialize pattern (e.g., OpenZeppelin's `ERC1967Proxy` constructor that calls `initialize` in the same transaction).

---

### Proof of Concept

```solidity
// Attacker script — runs before deployer's initialize tx is mined
IClearinghouse(clearinghouseProxy).initialize(
    attackerEndpoint,      // attacker-controlled endpoint
    attackerQuote,         // attacker-controlled quote token
    attackerLiqImpl,       // malicious delegatecall target
    0,
    attackerWithdrawPool   // all withdrawals redirected here
);
// Attacker is now owner; all user withdrawals go to attackerWithdrawPool.
```

After this call succeeds, every subsequent `withdrawCollateral` call through the legitimate sequencer routes funds to `attackerWithdrawPool` via `handleWithdrawTransfer`. [8](#0-7)

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

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L656-662)
```text
            txn
        );
        (bool success, bytes memory result) = clearinghouseLiq.delegatecall(
            liquidateSubaccountCall
        );
        require(success, string(result));
    }
```

**File:** core/contracts/ContractOwner.sol (L57-58)
```text
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
```

**File:** core/contracts/Verifier.sol (L37-39)
```text
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/Endpoint.sol (L31-46)
```text
    function initialize(
        address _sanctions,
        address _sequencer,
        address _offchainExchange,
        IClearinghouse _clearinghouse,
        address _verifier,
        address _endpointTx
    ) external initializer {
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
        clearinghouse = _clearinghouse;
        offchainExchange = _offchainExchange;
        verifier = IVerifier(_verifier);
        sanctions = ISanctionsList(_sanctions);
        endpointTx = _endpointTx;
```

**File:** core/contracts/OffchainExchange.sol (L243-258)
```text
    function initialize(address _clearinghouse, address _endpoint)
        external
        initializer
    {
        __Ownable_init();
        setEndpoint(_endpoint);

        __EIP712_init("Nado", "0.0.1");
        clearinghouse = IClearinghouse(_clearinghouse);
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
    }
```
