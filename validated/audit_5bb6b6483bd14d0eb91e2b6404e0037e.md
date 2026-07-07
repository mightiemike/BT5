### Title
Unchecked ERC-20 `transferFrom` Return Value Enables usdcE Drain Without Providing USDC - (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` using the raw (non-safe) variant and discards the returned `bool`. The function is publicly callable with no access control. If the USDC token returns `false` on failure instead of reverting, an attacker can drain usdcE from any victim's `DirectDepositV1` (DDA) contract without providing any USDC in exchange.

---

### Finding Description

`ContractOwner.replaceUsdcEWithUsdc()` is a migration helper intended to swap usdcE held in a DDA for USDC. The intended flow is:

1. Pull `balance` USDC from `msg.sender` into the DDA.
2. Withdraw usdcE from the DDA to `ContractOwner`.
3. Forward usdcE to `msg.sender`.

The critical flaw is at step 1:

```solidity
// core/contracts/ContractOwner.sol  L616
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

This uses the raw `transferFrom` interface — not `safeTransferFrom` from `ERC20Helper` — and the `bool` return value is silently discarded. [1](#0-0) 

Steps 2 and 3 proceed unconditionally regardless of whether the USDC pull succeeded:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));   // L617
IERC20Base(usdcE).safeTransfer(msg.sender, balance);              // L618
```

`DirectDepositV1.withdraw()` is `onlyOwner`, and `ContractOwner` is the owner of every DDA it deploys, so the withdrawal always succeeds. [2](#0-1) 

The function carries no access-control modifier — any externally-owned account can call it on Ink Chain (chainid 57073). [3](#0-2) 

By contrast, every other token movement in the codebase uses `ERC20Helper.safeTransferFrom`, which decodes the return value and reverts on `false`: [4](#0-3) 

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed transfer (e.g., insufficient balance or allowance) rather than reverting, an attacker with zero USDC can:

- Call `replaceUsdcEWithUsdc(victimSubaccount)` for any subaccount whose DDA holds usdcE.
- The `transferFrom` silently fails (returns `false`, ignored).
- The DDA's entire usdcE balance is withdrawn to `ContractOwner` and forwarded to the attacker.
- The DDA receives no USDC.

The corrupted asset delta is: attacker gains `balance` usdcE; DDA loses `balance` usdcE; no USDC is deposited. The DDA subaccount's collateral is permanently reduced by the stolen amount.

---

### Likelihood Explanation

The exploitability is conditional on the USDC token returning `false` rather than reverting. Circle's canonical USDC reverts on failure, which would prevent exploitation with the current deployment. However:

- The code pattern is unconditionally wrong and violates the protocol's own `safeTransferFrom` convention used everywhere else.
- Bridged or wrapped USDC variants on L2s sometimes differ in revert behavior.
- The function is permissionless, so no privilege escalation is required — any user can attempt it at zero cost.
- Any future token swap or redeployment with a non-reverting ERC-20 at that address would immediately be exploitable.

Likelihood: **Medium** (conditional on token behavior; zero attacker privilege required; no other mitigations present).

---

### Recommendation

Replace the raw `transferFrom` call with the protocol's own `safeTransferFrom` wrapper, which checks the return value and reverts on failure:

```solidity
// Before (ContractOwner.sol L616)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

Additionally, consider adding an `onlyOwner` modifier to `replaceUsdcEWithUsdc`, since it is a privileged migration operation and should not be callable by arbitrary users.

---

### Proof of Concept

1. A DDA for `victimSubaccount` holds 1000 usdcE.
2. Attacker (holding 0 USDC, 0 allowance) calls:
   ```
   ContractOwner.replaceUsdcEWithUsdc(victimSubaccount)
   ```
3. `IERC20Base(usdc).transferFrom(attacker, dda, 1000)` returns `false` — ignored.
4. `DirectDepositV1(dda).withdraw(usdcE)` transfers 1000 usdcE → `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, 1000)` transfers 1000 usdcE → attacker.
6. Attacker holds 1000 usdcE; DDA holds 0 usdcE and 0 USDC; victim's collateral is wiped. [5](#0-4)

### Citations

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```
