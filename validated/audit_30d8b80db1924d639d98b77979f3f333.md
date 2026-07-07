### Title
Cross-Chain Replay of `DirectDepositV1` Deployment via Fixed CREATE2 Salt - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.createDirectDepositV1` deploys `DirectDepositV1` contracts via CREATE2 using a hardcoded salt of `bytes32(uint256(1))` with no `chainId` component. If the protocol is deployed at the same address on multiple EVM chains (a common pattern for L2s using deterministic deployers), the DDA address for any given subaccount is identical across all chains. Because `createDirectDepositV1` and `creditDepositV1` are both permissionless (`public`/`external` with no access control), an attacker can replay a user's DDA deployment on a second chain and trigger fund-misdirection.

---

### Finding Description

In `ContractOwner.sol`, the `createDirectDepositV1` function deploys a `DirectDepositV1` contract using CREATE2:

```solidity
DirectDepositV1 directDepositV1 = new DirectDepositV1{
    salt: bytes32(uint256(1))
}(address(endpoint), address(spotEngine), subaccount, wrappedNative);
```

The salt is the constant `bytes32(uint256(1))`. The CREATE2 address is determined by `keccak256(0xff ++ deployer ++ salt ++ keccak256(init_code))`. Since `salt` never encodes `block.chainid`, and the `init_code` encodes only `(endpoint, spotEngine, subaccount, wrappedNative)` — all of which are identical across chains when the protocol is deployed deterministically — the resulting DDA address for a given `subaccount` is the same on every chain where the protocol is deployed at the same addresses.

Both entry points are permissionless:

- `createDirectDepositV1(bytes32 subaccount) public` — no modifier
- `creditDepositV1(bytes32 subaccount) external` — no modifier; auto-creates the DDA if absent, then calls `creditDeposit()`

`creditDeposit()` in `DirectDepositV1` iterates all spot product tokens, approves the endpoint, and calls `depositCollateralWithReferral` for the hardcoded `subaccount` on whichever chain's endpoint the DDA was deployed against.

---

### Impact Explanation

If the Nado protocol is deployed at the same contract addresses on a second EVM chain (e.g., a fork of Ink Chain, a testnet, or a future multi-chain expansion):

1. A user's DDA address on chain A is identical to the pre-image address on chain B.
2. A user who accidentally sends tokens to their DDA address on chain B (or who is front-run by an attacker calling `creditDepositV1` after tokens arrive at that address on chain B) will have those tokens deposited into their subaccount on chain B's endpoint — not chain A's.
3. The user's funds are now locked in the wrong chain's protocol. Depending on withdrawal queue state, liquidity, or the user's ability to operate on chain B, these funds may be effectively inaccessible.

The corrupted state delta is: user's collateral balance on chain B's `SpotEngine` is credited instead of chain A's, and the tokens are consumed from the DDA with no recourse.

---

### Likelihood Explanation

Ink Chain is an EVM-compatible L2. Deterministic deployment via CREATE2 factories (e.g., `0x4e59b44847b379578588920cA78FbF26c0B4956C`) is standard practice and produces identical contract addresses across EVM chains. The protocol already uses a hardcoded bytecode hash check (`0x7974df41...`) that implies a controlled, reproducible deployment. A fork of Ink Chain or a future multi-chain deployment would immediately expose this path. The `creditDepositV1` entry point requires no privilege, making the trigger trivially reachable by any caller.

---

### Recommendation

Include `block.chainid` in the CREATE2 salt so that DDA addresses are chain-specific:

```solidity
bytes32 salt = keccak256(abi.encode(block.chainid, subaccount));
DirectDepositV1 directDepositV1 = new DirectDepositV1{salt: salt}(
    address(endpoint), address(spotEngine), subaccount, wrappedNative
);
```

This ensures that even if the protocol is deployed at identical addresses on multiple chains, DDA addresses diverge per chain and cannot be replayed.

---

### Proof of Concept

1. Protocol is deployed at identical addresses on Ink Chain (chain A, id=57073) and a fork (chain B).
2. User calls `creditDepositV1(subaccount)` on chain A; DDA is deployed at address `X` and stored in `directDepositV1Address[subaccount]`.
3. User sends 10,000 USDC to address `X` on chain B, intending to fund their chain A subaccount.
4. Attacker (or anyone) calls `creditDepositV1(subaccount)` on chain B's `ContractOwner`.
5. `createDirectDepositV1` deploys a new `DirectDepositV1` at address `X` on chain B (same salt, same deployer address, same init_code → same address).
6. `creditDeposit()` is called: the DDA approves chain B's endpoint and calls `depositCollateralWithReferral(subaccount, productId, 10000e6, "-1")` on chain B.
7. User's 10,000 USDC is now credited to their subaccount on chain B's `SpotEngine`. Their chain A balance is unaffected. Funds are on the wrong chain. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/ContractOwner.sol (L486-500)
```text
    function createDirectDepositV1(bytes32 subaccount)
        public
        returns (address payable)
    {
        require(
            getDirectDepositV1BytecodeHash() ==
                0x7974df41bdca2be1539fa7d01f41277f0d728823b20230a18a31e40c707874e7,
            "dda hash"
        );
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
        directDepositV1Address[subaccount] = payable(directDepositV1);
        return payable(directDepositV1);
    }
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```

**File:** core/contracts/DirectDepositV1.sol (L42-62)
```text
    constructor(
        address _endpoint,
        address _spotEngine,
        bytes32 _subaccount,
        address payable _wrappedNative
    ) {
        endpoint = IIEndpoint(_endpoint);
        spotEngine = IISpotEngine(_spotEngine);
        subaccount = _subaccount;
        wrappedNative = _wrappedNative;
        uint256 balance = address(this).balance;
        if (balance != 0) {
            // shouldn't revert even if the transfer fails, otherwise the funds
            // will be stuck in the DDA forever.
            (bool success, ) = wrappedNative.call{value: balance}("");
            if (!success) {
                emit NativeTokenTransferFailed(balance);
            }
        }
        emit DirectDepositV1Created(version(), subaccount, address(this));
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-101)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
        }
    }
```
