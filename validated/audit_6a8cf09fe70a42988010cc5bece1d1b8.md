### Title
Unprotected `Verifier.initialize()` Allows Anyone to Frontrun Initialization and Set Malicious Sequencer Public Keys — (File: `core/contracts/Verifier.sol`)

---

### Summary

`Verifier.initialize()` is declared `external initializer` with no caller restriction. Any unprivileged address can race the deployer's initialization transaction, become the contract owner, and install arbitrary Schnorr public keys. Because every sequencer-signed transaction processed by `Endpoint` is validated through `Verifier.requireValidSignature()`, a successful frontrun gives the attacker full control over what the protocol accepts as a valid sequencer instruction.

---

### Finding Description

`Verifier.initialize()` carries the OpenZeppelin `initializer` guard (one-time execution) but imposes no restriction on *who* may be the first caller:

```solidity
// core/contracts/Verifier.sol  lines 41-48
function initialize(Point[8] memory initialSet) external initializer {
    __Ownable_init();                          // msg.sender becomes owner
    for (uint256 i = 0; i < 8; ++i) {
        if (!isPointNone(initialSet[i])) {
            _assignPubkey(i, initialSet[i].x, initialSet[i].y);
        }
    }
}
``` [1](#0-0) 

`__Ownable_init()` unconditionally promotes `msg.sender` to owner. [2](#0-1) 

All subsequent privileged key-management operations (`assignPubKey`, `deletePubkey`) are gated by `onlyOwner`, so whoever wins the initialization race permanently controls the key set. [3](#0-2) 

The public keys installed during `initialize()` are the sole inputs to `requireValidSignature()`, which is the protocol's only check that a batch of sequencer transactions is authentic:

```solidity
// core/contracts/Verifier.sol  lines 140-158
function requireValidSignature(
    bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask
) public {
    require(checkQuorum(signerBitmask));
    Point memory pubkey = getAggregatePubkey(signerBitmask);
    require(verify(...), "Verification failed");
}
``` [4](#0-3) 

`checkQuorum` counts only keys that are non-zero in `pubkeys[]`, so an attacker who installs exactly one key they control satisfies the quorum check with a single signer bitmask. [5](#0-4) 

---

### Impact Explanation

`Verifier` is the cryptographic root of trust for the entire Nado sequencer pipeline. `Endpoint` passes every submitted sequencer batch through `verifier.requireValidSignature()` before executing it. An attacker who controls the installed public keys can:

- Forge valid Schnorr signatures for any sequencer transaction type (withdrawals, liquidations, balance updates, NLP mint/burn).
- Drain all user collateral via fraudulent `WithdrawCollateral` or `WithdrawCollateralV2` transactions that pass signature validation.
- Corrupt per-subaccount balances in `SpotEngine` and `PerpEngine` by injecting arbitrary `updateBalance` calls through the sequencer path.

The attacker also becomes the permanent `owner` of `Verifier`, so even after the legitimate deployer notices the frontrun, they cannot reclaim the key set — `assignPubKey` and `deletePubkey` are both `onlyOwner`. [3](#0-2) 

---

### Likelihood Explanation

The attack requires only:
1. Watching the mempool for the proxy deployment + `initialize()` call.
2. Resubmitting `initialize()` with a higher gas price before the deployer's transaction is mined.

No special privilege, capital, or protocol knowledge is needed. The frontrun window is the standard block-inclusion race on any EVM chain. This is a well-known, mechanically straightforward attack class.

---

### Recommendation

Add a deployer/owner check identical to the pattern already used in `ContractOwner.initialize()`:

```solidity
// ContractOwner.sol line 58 — existing safe pattern
require(_deployer == msg.sender, "expected deployed to initialize");
```

For `Verifier`, the simplest fix is to pass the expected initializer address as a constructor argument stored before `_disableInitializers()` is called, or to check `msg.sender` against a known deployer address inside `initialize()`. Alternatively, use OpenZeppelin's `Ownable2Step` and set the initial owner in the constructor of the implementation (before `_disableInitializers()`), then restrict `initialize()` to `onlyOwner`. [6](#0-5) 

---

### Proof of Concept

```
1. Deployer broadcasts: proxy.deploy() + verifier.initialize(legitimateKeys)
2. Attacker sees the pending tx in the mempool.
3. Attacker broadcasts: verifier.initialize(attackerKeys) with higher gas.
4. Attacker's tx is mined first.
   - pubkeys[0] = attacker's Schnorr key
   - nSigner = 1
   - owner = attacker
5. Deployer's tx reverts (initializer already executed).
6. Attacker calls verifier.requireValidSignature() with a self-signed message
   → checkQuorum(0x01) passes (1 signer, nSigner=1, 1*2 > 1 ✓)
   → Schnorr verify passes (attacker signed with their own key)
7. Attacker submits a sequencer batch containing WithdrawCollateral for all
   user subaccounts, pointing to attacker-controlled addresses.
8. Endpoint accepts the batch; all user funds are drained.
``` [1](#0-0) [5](#0-4) [7](#0-6)

### Citations

**File:** core/contracts/Verifier.sol (L41-48)
```text
    function initialize(Point[8] memory initialSet) external initializer {
        __Ownable_init();
        for (uint256 i = 0; i < 8; ++i) {
            if (!isPointNone(initialSet[i])) {
                _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            }
        }
    }
```

**File:** core/contracts/Verifier.sol (L61-66)
```text
    function assignPubKey(
        uint256 i,
        uint256 x,
        uint256 y
    ) public onlyOwner {
        _assignPubkey(i, x, y);
```

**File:** core/contracts/Verifier.sol (L69-83)
```text
    function _assignPubkey(
        uint256 i,
        uint256 x,
        uint256 y
    ) internal {
        require(i < 8);
        if (isPointNone(pubkeys[i])) {
            nSigner += 1;
        }
        pubkeys[i] = Point(x, y);
        for (uint256 s = (1 << i); s < 256; s = (s + 1) | (1 << i)) {
            isAggregatePubkeyLatest[s] = false;
        }
        emit AssignPubKey(i, x, y);
    }
```

**File:** core/contracts/Verifier.sol (L126-138)
```text
    function checkQuorum(uint8 signerBitmask) internal view returns (bool) {
        uint256 nSigned = 0;
        for (uint256 i = 0; i < 8; ++i) {
            bool signed = ((signerBitmask >> i) & 1) == 1;
            if (signed) {
                if (isPointNone(pubkeys[i])) {
                    return false;
                }
                nSigned += 1;
            }
        }
        return nSigned * 2 > nSigner;
    }
```

**File:** core/contracts/Verifier.sol (L140-158)
```text
    function requireValidSignature(
        bytes32 message,
        bytes32 e,
        bytes32 s,
        uint8 signerBitmask
    ) public {
        require(checkQuorum(signerBitmask));
        Point memory pubkey = getAggregatePubkey(signerBitmask);
        require(
            verify(
                pubkey.y % 2 == 0 ? 27 : 28,
                bytes32(pubkey.x),
                message,
                e,
                s
            ),
            "Verification failed"
        );
    }
```

**File:** core/contracts/ContractOwner.sol (L57-58)
```text
    ) external initializer {
        require(_deployer == msg.sender, "expected deployed to initialize");
```
