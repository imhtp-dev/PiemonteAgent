"""
Enhanced Fuzzy Search Implementation
Much better than simple string matching
"""

import logging
from typing import List, Set, Tuple, Dict, Any
from rapidfuzz import fuzz, process
from models.requests import HealthService, ServiceSearchResponse
from services.local_data_service import local_data_service
from services.config import config

logger = logging.getLogger(__name__)

class ServiceSearchError(Exception):
    """Custom exception for service search errors"""
    pass

class FuzzySearchService:
    """Enhanced fuzzy search for health services"""
    
    def __init__(self):
        self._services_cache: List[HealthService] = None
        self._cache_time: float = 0
        self._cache_expiry_seconds = config.CACHE_EXPIRY_HOURS * 3600
    
    def _get_services(self) -> List[HealthService]:
        """Get cached services or fetch fresh ones from local data"""
        import time
        current_time = time.time()

        if (self._services_cache is None or
            (current_time - self._cache_time) > self._cache_expiry_seconds):
            logger.info("Loading services from local data for fuzzy search")
            try:
                self._services_cache = local_data_service.get_health_services()
                self._cache_time = current_time
                logger.info(f"Cached {len(self._services_cache)} services from local data for fuzzy search")
            except Exception as e:
                logger.error(f"Failed to load services from local data: {e}")
                if self._services_cache is None:
                    self._services_cache = []

        return self._services_cache
    
    def _expand_search_terms(self, search_term: str) -> Set[str]:
        """
        Process Italian search terms and create variations
        Enhanced version with better term extraction
        """
        search_terms = {search_term.lower().strip()}

        # Split search term into individual words and clean them
        words = [word.strip() for word in search_term.lower().split() if word.strip()]
        search_terms.update(words)

        # Handle multi-word terms and normalize
        normalized_search = search_term.lower().replace("-", " ").replace("_", " ")
        search_terms.add(normalized_search)

        # Clean empty terms
        search_terms = {term for term in search_terms if term.strip()}

        logger.debug(f"Processed '{search_term}' to: {search_terms}")
        return search_terms
    
    def _create_service_search_text(self, service: HealthService) -> str:
        """Create comprehensive searchable text for a service"""
        text_parts = [
            service.name,
            service.code,
            *service.synonyms
        ]
        
        # Join and clean
        search_text = " ".join(part for part in text_parts if part).lower()
        return search_text
    
    def _calculate_service_score(self, service: HealthService, search_terms: Set[str], original_query: str) -> float:
        """
        Calculate comprehensive fuzzy score for a service
        
        Args:
            service: Health service to score
            search_terms: Expanded search terms
            original_query: Original user query
            
        Returns:
            Score between 0-100
        """
        service_text = self._create_service_search_text(service)
        service_name = service.name.lower()
        
        # Extract key medical terms from query
        query_words = set(original_query.lower().split())
        medical_keywords = {"radiografia", "rx", "caviglia", "cuore", "sangue", "denti", "cardiologia", "analisi", "esame", "tc", "tac", "tomografia"}
        
        scores = []
        
        # 1. Exact keyword matching (highest priority for medical terms)
        exact_keyword_score = 0
        for term in search_terms:
            if term in service_text:
                # Higher bonus for medical keywords
                if term in medical_keywords:
                    exact_keyword_score += 25
                else:
                    exact_keyword_score += 15
        scores.append(min(exact_keyword_score, 80))  # Max 80 points for exact matches
        
        # 2. Fuzzy match with original query
        name_ratio = fuzz.partial_ratio(original_query.lower(), service_name)
        text_ratio = fuzz.partial_ratio(original_query.lower(), service_text)
        scores.append(max(name_ratio, text_ratio) * 0.3)  # 30% weight
        
        # 3. Token-based matching (handles word order differences)  
        token_ratio = fuzz.token_sort_ratio(original_query.lower(), service_name)
        scores.append(token_ratio * 0.2)  # 20% weight
        
        # 4. Individual word matching
        word_match_score = 0
        for word in query_words:
            if word in service_text:
                word_match_score += 15
        scores.append(min(word_match_score, 30))  # Max 30 points for word matches
        
        # 5. Penalty for irrelevant results
        penalty = 0
        irrelevant_terms = {"peeling", "gemellare", "fetale", "pediatrica"}
        for irrelevant in irrelevant_terms:
            if irrelevant in service_name:
                penalty -= 20
        
        final_score = sum(scores) + penalty
        final_score = max(0, final_score)  # Ensure non-negative
        
        # Debug logging for common Italian medical terms
        if "caviglia" in original_query.lower():
            if "caviglia" in service_text:
                logger.debug(f"Caviglia match: {service.name} -> {final_score:.1f}")
        
        return final_score
    
    def search(self, search_term: str, limit: int = None) -> ServiceSearchResponse:
        """Alias for search_services for compatibility"""
        return self.search_services(search_term, limit)

    def search_services(self, search_term: str, limit: int = None) -> ServiceSearchResponse:
        """
        Enhanced fuzzy search for health services
        
        Args:
            search_term: User's search query
            limit: Maximum number of results
            
        Returns:
            ServiceSearchResponse with best matching services
        """
        if limit is None:
            limit = config.DEFAULT_SEARCH_LIMIT
        
        logger.info(f"Fuzzy searching for: '{search_term}' (limit: {limit})")
        
        if not search_term or len(search_term.strip()) < 2:
            return ServiceSearchResponse(
                found=False,
                count=0,
                services=[],
                search_term=search_term,
                message="Search term too short. Please provide at least 2 characters."
            )
        
        try:
            all_services = self._get_services()
            if not all_services:
                return ServiceSearchResponse(
                    found=False,
                    count=0,
                    services=[],
                    search_term=search_term,
                    message="No services available for search."
                )
            
            # Expand search terms
            search_terms = self._expand_search_terms(search_term)
            logger.debug(f"Expanded search terms: {search_terms}")
            
            # Score all services
            scored_services = []
            
            for service in all_services:
                score = self._calculate_service_score(service, search_terms, search_term)
                
                # Only include services above threshold
                if score >= 40:  # Minimum threshold
                    scored_services.append((service, score))
            
            # Sort by score (highest first)
            scored_services.sort(key=lambda x: x[1], reverse=True)
            
            # Take top results
            top_services = [service for service, score in scored_services[:limit]]
            
            logger.info(f"Fuzzy search found {len(top_services)} matching services")
            
            if top_services and logger.isEnabledFor(logging.DEBUG):
                logger.debug("Top fuzzy search results:")
                for i, (service, score) in enumerate(scored_services[:3], 1):
                    logger.debug(f"  {i}. {service.name} (score: {score:.1f})")
            
            return ServiceSearchResponse(
                found=len(top_services) > 0,
                count=len(top_services),
                services=top_services,
                search_term=search_term,
                message=None if top_services else self._get_no_results_message(search_term)
            )
            
        except Exception as e:
            logger.error(f"Fuzzy search failed: {e}")
            return ServiceSearchResponse(
                found=False,
                count=0,
                services=[],
                search_term=search_term,
                message=f"Search failed: {str(e)}"
            )
    
    def _get_no_results_message(self, search_term: str) -> str:
        """Generate helpful message when no results found"""
        suggestions = [
            "cardiologia (servizi cardiaci)",
            "analisi del sangue (esami del sangue)",
            "radiografia (servizi di imaging)",
            "dentale (servizi dentali)",
            "caviglia (esami della caviglia)"
        ]
        
        return (
            f"Nessun servizio trovato per '{search_term}'. "
            f"Prova a cercare: {', '.join(suggestions)}"
        )

# Global fuzzy search service instance
fuzzy_search_service = FuzzySearchService()