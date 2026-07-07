### Title
Initialization Frontrunning Allows Attacker to Seize Ownership and Control of `Endpoint` - (File: core/contracts/Endpoint.sol)

---

### Summary

`Endpoint.sol` lacks a constructor with `_disableInitializers()` and places no caller restriction on its `initialize()` function. Between proxy deployment and the legitimate `initialize()` call, an unprivileged attacker can frontrun the transaction, become the contract owner, and inject a malicious `sequencer` and `verifier`, seizing full control over the protocol's transaction processing pipeline.

---

### Finding Description

`Endpoint` is an upgradeable contract that inherits `OwnableUpgradeable` and exposes an `initialize()` function protected only by the `initializer` modifier. Unlike several other Nado contracts (`ContractOwner`, `Verifier`, `Airdrop`, `BaseWithdrawPool`, `BaseProxyManager`) that correctly call `_disableInitializers()` in their constructors, `Endpoint` has no constructor at all. [1](#0-0) 

The `initialize()` function unconditionally calls `__Ownable_init()`, granting ownership to `msg.sender`, and then stores caller-supplied addresses for `sequencer`, `offchainExchange`, `verifier`, and `clearinghouse` with no restriction on who may call it. [2](#0-1) 

Compare this to `ContractOwner`, which correctly guards its `initialize()` with both `_disableInitializers()` in the constructor and an explicit `require(_deployer == msg.sender)` check: [3](#0-2) 

The same missing-constructor pattern also affects `Clearinghouse` and `OffchainExchange`: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

If an attacker successfully frontruns `Endpoint.initialize()`, they:

1. Become the contract owner via `__Ownable_init()`.
2. Set `sequencer` to their own address — `submitTransactionsChecked` enforces `require(msg.sender == sequencer)`, so the attacker becomes the sole authorized submitter of sequenced transaction batches.
3. Set `verifier` to a malicious contract that always passes `requireValidSignature`, bypassing the Schnorr multi-sig quorum check entirely.
4. Set `offchainExchange` to a malicious contract, corrupting all order-matching balance updates routed through `matchOrders`. [6](#0-5) 

The attacker can then call `submitTransactionsChecked` with arbitrary transaction batches — including `WithdrawCollateral`, `DepositCollateral`, `LiquidateSubaccount`, and `WithdrawInsurance` — draining user funds and corrupting protocol state. If the protocol team deploys without detecting the compromise, all users interact with an attacker-controlled system from the outset.

---

### Likelihood Explanation

Deployment transactions are publicly visible in the mempool. An attacker monitoring for proxy deployments of known Nado contract bytecode can immediately submit a higher-gas `initialize()` call. This requires no special privilege, no leaked keys, and no social engineering — only a standard MEV/frontrunning capability available to any mempool observer. The window exists on every deployment or redeployment of the `Endpoint` proxy.

---

### Recommendation

1. Add a constructor with `_disableInitializers()` to `Endpoint`, `Clearinghouse`, and `OffchainExchange`, matching the pattern already used in `ContractOwner`, `Verifier`, `Airdrop`, `BaseWithdrawPool`, and `BaseProxyManager`:

```solidity
/// @custom:oz-upgrades-unsafe-allow constructor
constructor() {
    _disableInitializers();
}
```

2. Add a caller restriction to `Endpoint.initialize()` (e.g., require `msg.sender` equals a deployer address set at construction time, or use a factory-based deployment that atomically deploys and initializes in a single transaction).

---

### Proof of Concept

1. Attacker deploys a malicious `Verifier`-compatible contract `MaliciousVerifier` whose `requireValidSignature` always returns without reverting.
2. Attacker monitors the mempool for the Nado `Endpoint` proxy deployment transaction.
3. Attacker submits a higher-gas call to `Endpoint.initialize(sanctions, attacker_address, malicious_offchain_exchange, clearinghouse, malicious_verifier, endpointTx)`.
4. Attacker's `initialize()` executes first: `__Ownable_init()` sets `owner = attacker`; `sequencer = attacker`; `verifier = MaliciousVerifier`.
5. The legitimate deployer's `initialize()` call reverts with `Initializable: contract is already initialized`.
6. Attacker calls `submitTransactionsChecked(0, [withdrawCollateralTx], e, s, bitmask)` — `MaliciousVerifier.requireValidSignature` passes, and the attacker-crafted `WithdrawCollateral` transaction drains user collateral. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L31-66)
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
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
        slowModeConfig = SlowModeConfig({timeout: 0, txCount: 0, txUpTo: 0});
        priceX18[QUOTE_PRODUCT_ID] = ONE;

        if (nlpPools.length == 0) {
            nlpPools.push(
                NlpPool({
                    poolId: 0,
                    subaccount: N_ACCOUNT,
                    owner: address(0),
                    balanceWeightX18: uint128(ONE)
                })
            );
        }
    }
```

**File:** core/contracts/Endpoint.sol (L271-294)
```text
    function submitTransactionsChecked(
        uint64 idx,
        bytes[] calldata transactions,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) external {
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
        // TODO: if one of these transactions fails this means the sequencer is in an error state
        // we should probably record this, and engage some sort of recovery mode

        bytes32 digest = keccak256(abi.encode(idx));
        for (uint256 i = 0; i < transactions.length; ++i) {
            digest = keccak256(abi.encodePacked(digest, transactions[i]));
        }
        verifier.requireValidSignature(digest, e, s, signerBitmask);

        for (uint256 i = 0; i < transactions.length; i++) {
            bytes calldata transaction = transactions[i];
            processTransaction(transaction);
            nSubmissions += 1;
        }
    }
```

**File:** core/contracts/ContractOwner.sol (L43-68)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(
        address multisig,
        address _deployer,
        address _spotEngine,
        address _perpEngine,
        address _endpoint,
        address _clearinghouse,
        address _verifier,
        address payable _wrappedNative
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
        spotEngine = SpotEngine(_spotEngine);
        perpEngine = PerpEngine(_perpEngine);
        endpoint = Endpoint(_endpoint);
        clearinghouse = IClearinghouse(_clearinghouse);
        verifier = Verifier(_verifier);
        wrappedNative = _wrappedNative;
    }
```

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

**File:** core/contracts/Verifier.sol (L36-48)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(Point[8] memory initialSet) external initializer {
        __Ownable_init();
        for (uint256 i = 0; i < 8; ++i) {
            if (!isPointNone(initialSet[i])) {
                _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            }
        }
    }
```
